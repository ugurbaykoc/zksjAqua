"""BLE transport for a single ZKSJ AQUA pump.

The pump exposes one write characteristic and one notify characteristic and
speaks a request/response protocol over them.  This module owns the link: it
connects on demand, serialises commands, matches each write to the
notification it provokes, and drops the link once things go quiet so the phone
app can still reach the pump.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from .const import COMMAND_TIMEOUT, DISCONNECT_DELAY
from .protocol import DeviceProfile, FrameError, PumpState, WaveMode, ZksjError

_LOGGER = logging.getLogger(__name__)


class ZksjConnectionError(ZksjError):
    """The pump could not be reached."""


class ZksjPump:
    """One pump, and the connection to it."""

    def __init__(self, ble_device: BLEDevice, profile: DeviceProfile) -> None:
        self._ble_device = ble_device
        self._profile = profile
        self._state = PumpState()

        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._reply: asyncio.Future[bytes] | None = None
        self._listeners: list[Callable[[PumpState], None]] = []
        self._expected_disconnect = False

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def name(self) -> str:
        return self._ble_device.name or self._ble_device.address

    @property
    def profile(self) -> DeviceProfile:
        return self._profile

    @property
    def state(self) -> PumpState:
        return self._state

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher BLEDevice seen by Home Assistant's BLE stack."""
        self._ble_device = ble_device

    def add_listener(self, callback: Callable[[PumpState], None]) -> Callable[[], None]:
        """Subscribe to state changes.  Returns an unsubscribe callable."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    # -- commands ---------------------------------------------------------

    async def async_refresh(self) -> PumpState:
        await self._command(self._profile.codec.encode_query())
        return self._state

    async def async_set_power(self, on: bool) -> None:
        await self._command(self._profile.codec.encode_power(on))

    async def async_set_speed(self, level: int) -> None:
        low, high = self._profile.min_speed, self._profile.max_speed
        if not low <= level <= high:
            raise ValueError(f"speed {level} outside {low}-{high}")
        await self._command(self._profile.codec.encode_speed(level))

    async def async_set_mode(self, key: str) -> None:
        mode: WaveMode = self._profile.mode_by_key(key)
        await self._command(self._profile.codec.encode_mode(mode))

    async def async_set_feed(self, on: bool) -> None:
        await self._command(self._profile.codec.encode_feed(on))

    async def async_disconnect(self) -> None:
        """Tear the link down for good, e.g. when the entry unloads."""
        self._cancel_disconnect_timer()
        self._expected_disconnect = True
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except BleakError as err:  # pragma: no cover - best effort
                _LOGGER.debug("%s: error while disconnecting: %s", self.name, err)

    # -- plumbing ---------------------------------------------------------

    async def _command(self, frame: bytes) -> None:
        """Send one frame and wait for the pump to answer."""
        async with self._command_lock:
            client = await self._ensure_connected()
            loop = asyncio.get_running_loop()
            self._reply = loop.create_future()
            try:
                await client.write_gatt_char(
                    self._profile.write_uuid, frame, response=False
                )
                await asyncio.wait_for(self._reply, COMMAND_TIMEOUT)
            except TimeoutError as err:
                # A pump that stops answering is usually a half-open link;
                # drop it so the next command reconnects instead of timing
                # out again.
                await self._drop_connection()
                raise ZksjConnectionError(
                    f"{self.name} did not answer within {COMMAND_TIMEOUT}s"
                ) from err
            except BleakError as err:
                await self._drop_connection()
                raise ZksjConnectionError(f"{self.name}: {err}") from err
            finally:
                self._reply = None
            self._schedule_disconnect()

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            self._cancel_disconnect_timer()
            return self._client

        async with self._connect_lock:
            # Another command may have connected while we waited.
            if self._client is not None and self._client.is_connected:
                return self._client

            _LOGGER.debug("%s: connecting", self.name)
            self._expected_disconnect = False
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self.name,
                    self._on_disconnected,
                    use_services_cache=True,
                    ble_device_callback=lambda: self._ble_device,
                )
                await client.start_notify(
                    self._profile.notify_uuid, self._on_notification
                )
            except BleakNotFoundError as err:
                raise ZksjConnectionError(
                    f"{self.name} was not found; is it in range and in app mode?"
                ) from err
            except BleakError as err:
                raise ZksjConnectionError(f"{self.name}: {err}") from err

            self._client = client
            return client

    def _on_notification(self, _sender: int, data: bytearray) -> None:
        payload = bytes(data)
        try:
            state = self._profile.codec.decode(payload, self._state)
        except FrameError as err:
            _LOGGER.debug("%s: ignoring frame %s: %s", self.name, payload.hex(), err)
            return

        self._state = state
        if self._reply is not None and not self._reply.done():
            self._reply.set_result(payload)
        for listener in list(self._listeners):
            listener(state)

    def _on_disconnected(self, _client: BleakClientWithServiceCache) -> None:
        self._client = None
        if self._reply is not None and not self._reply.done():
            self._reply.set_exception(
                ZksjConnectionError(f"{self.name} disconnected mid-command")
            )
        if self._expected_disconnect:
            _LOGGER.debug("%s: disconnected", self.name)
        else:
            _LOGGER.debug("%s: unexpectedly disconnected", self.name)

    async def _drop_connection(self) -> None:
        self._expected_disconnect = True
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except BleakError:  # pragma: no cover - best effort
                pass

    def _schedule_disconnect(self) -> None:
        self._cancel_disconnect_timer()
        loop = asyncio.get_running_loop()
        self._disconnect_timer = loop.call_later(
            DISCONNECT_DELAY, lambda: asyncio.ensure_future(self._idle_disconnect())
        )

    def _cancel_disconnect_timer(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    async def _idle_disconnect(self) -> None:
        self._disconnect_timer = None
        if self._command_lock.locked():
            # A command started while the timer was firing; it will re-arm.
            return
        _LOGGER.debug("%s: idle, releasing the link", self.name)
        await self._drop_connection()
