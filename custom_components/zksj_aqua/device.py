"""Local transport to a ZKSJ AQUA pump.

The pump speaks Tuya's local protocol on port 6668 -- the same wire format
the vendor app ends up using, minus the cloud round trip.  ``tinytuya``
implements that protocol but is synchronous and not thread safe, so this
module owns exactly one device object, serialises access to it behind a lock,
and runs every call in the executor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

import tinytuya
from homeassistant.core import HomeAssistant

from .cloud import ZksjCloudError, ZksjCloudTransport
from .const import (
    CLOUD_PRIME_DELAY,
    CONNECTION_TIMEOUT,
    MQTT_FRAME_PREFIX,
    PROTOCOL_VERSIONS,
    TUYA_LOCAL_PORT,
)
from .protocol import (
    DP_CUR_MODE,
    DP_FEED,
    DP_GET_MODE,
    encode_get_mode,
    from_wire,
    to_wire,
)

_LOGGER = logging.getLogger(__name__)




class ZksjConnectionError(Exception):
    """The pump could not be reached, or rejected our credentials."""


class ZksjDevice:
    """One pump, and the local socket to it."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str,
        device_id: str,
        local_key: str,
        version: str,
    ) -> None:
        self._hass = hass
        self.host = host
        self.device_id = device_id
        self._local_key = local_key
        self._version = version
        self._device: tinytuya.Device | None = None

    @property
    def version(self) -> str:
        return self._version

    @property
    def has_local_key(self) -> bool:
        """Whether we hold the credential needed to read or write data points."""
        return bool(self._local_key)

    def _build(self) -> tinytuya.Device:
        device = tinytuya.Device(
            self.device_id,
            address=self.host,
            local_key=self._local_key,
            version=float(self._version),
            connection_timeout=CONNECTION_TIMEOUT,
        )
        # One long-lived socket: the pump is slow to accept new connections,
        # and reconnecting per command turns a slider drag into a stutter.
        device.set_socketPersistent(True)
        device.set_socketRetryLimit(1)
        return device

    def _ensure(self) -> tinytuya.Device:
        if self._device is None:
            self._device = self._build()
        return self._device

    # -- executor-side calls ---------------------------------------------

    def _status(self) -> dict[str, Any]:
        device = self._ensure()
        result = device.status()
        return _translate(_unwrap(result))

    def _set(self, dp: str, value: Any) -> dict[str, Any]:
        device = self._ensure()
        result = device.set_value(dp, to_wire(dp, value))
        # A set can come back empty; that is not an error, just no echo.
        if not result:
            return {}
        return _translate(_unwrap(result, allow_empty=True))

    def _close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except OSError as err:  # pragma: no cover - best effort
                _LOGGER.debug("%s: error closing socket: %s", self.host, err)
            self._device = None

    # -- async surface ----------------------------------------------------

    async def async_status(self) -> dict[str, Any]:
        """Read every data point the pump will volunteer."""
        return await self._hass.async_add_executor_job(self._status)

    async def async_set(self, dp: str, value: Any) -> dict[str, Any]:
        """Write one data point."""
        return await self._hass.async_add_executor_job(self._set, dp, value)

    async def async_request_dp(self, dp: str) -> dict[str, Any]:
        """Ask the pump to report a DP it did not include in its status.

        The program (DP 101) in particular is often absent from a plain
        status read; DP 106 is the vendor's own way of asking for it.
        """
        return await self.async_set(DP_GET_MODE, encode_get_mode(int(dp)))

    async def async_is_reachable(self) -> bool:
        """Is the pump powered up and on the network?

        Opening the Tuya control port answers that without any credentials,
        which is the whole of what can be known about a pump whose local key
        we do not have.  It says nothing about whether the pump is running:
        one switched off over its own data point still answers here.
        """
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, TUYA_LOCAL_PORT),
                CONNECTION_TIMEOUT,
            )
            return True
        except (OSError, TimeoutError):
            return False
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError, TimeoutError):
                    await writer.wait_closed()

    async def async_close(self) -> None:
        """Release the socket."""
        await self._hass.async_add_executor_job(self._close)

    async def async_refresh_all(self, *, need: tuple[str, ...] = ()) -> dict[str, Any]:
        """Status, plus an active nudge for any DP listed in ``need``.

        A plain status() read is one round trip; each DP in ``need`` that
        status() left out costs another. On a pump with a weak link, every
        extra round trip is another chance for a dropped or garbled packet,
        so callers should only ask for what they do not already have --
        DP 101 in particular never changes except when this integration
        writes it, so it is worth requesting once, not every poll.
        """
        dps = await self.async_status()
        for dp in need:
            if dp in dps:
                continue
            try:
                extra = await self.async_request_dp(dp)
            except ZksjConnectionError:
                raise
            except Exception as err:  # noqa: BLE001 - tinytuya raises broadly
                _LOGGER.debug("%s: DP %s request failed: %s", self.host, dp, err)
                continue
            dps.update(extra)
        if DP_FEED in dps:
            _LOGGER.debug("%s: feed DP present: %s", self.host, dps[DP_FEED])
        return dps


class ZksjCloudDevice:
    """One pump, reached over Tuya's MQTT broker rather than the LAN.

    Presents the same surface as :class:`ZksjDevice` so the coordinator does
    not have to care which transport it was given, but the shape underneath
    is different: MQTT pushes state instead of answering polls, so reads are
    served from whatever the pump last reported and :meth:`async_set` gets no
    reply of its own -- the echo arrives on the subscription a moment later,
    through ``on_update``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        device_id: str,
        local_key: str,
        broker: str,
        port: int,
        client_id: str,
        username: str,
        password: str,
        source_id: int,
    ) -> None:
        self._hass = hass
        self.host = broker
        self.device_id = device_id
        self._local_key = local_key
        self._dps: dict[str, Any] = {}
        self._primed = False
        self.on_update: Callable[[dict[str, Any]], None] | None = None

        self._transport = ZksjCloudTransport(
            hass,
            device_id=device_id,
            local_key=local_key,
            broker=broker,
            port=port,
            client_id=client_id,
            username=username,
            password=password,
            source_id=source_id,
        )
        self._transport.set_listener(self._handle_push)

    @property
    def version(self) -> str:
        """The framing version the broker speaks, for display only."""
        return MQTT_FRAME_PREFIX.decode()

    @property
    def has_local_key(self) -> bool:
        """The key is what decrypts the payloads, so it is required here too."""
        return bool(self._local_key)

    def _handle_push(self, dps: dict[str, Any]) -> None:
        """A state report arrived on the subscription."""
        translated = _translate(dps)
        self._dps.update(translated)
        if self.on_update is not None:
            self.on_update(dict(self._dps))

    # -- async surface ----------------------------------------------------

    async def async_connect(self) -> None:
        try:
            await self._transport.async_connect()
        except ZksjCloudError as err:
            raise ZksjConnectionError(str(err)) from err

    async def async_status(self) -> dict[str, Any]:
        """Whatever the pump has reported so far."""
        return dict(self._dps)

    async def async_set(self, dp: str, value: Any) -> dict[str, Any]:
        """Write one data point.

        Returns nothing to apply: unlike the local protocol there is no ack
        carrying the new value, so callers fall back to waiting for the push.
        """
        try:
            await self._transport.async_publish({dp: to_wire(dp, value)})
        except ZksjCloudError as err:
            raise ZksjConnectionError(str(err)) from err
        return {}

    async def async_request_dp(self, dp: str) -> dict[str, Any]:
        """Ask the pump to report a DP, the way the app does on connect."""
        return await self.async_set(DP_GET_MODE, encode_get_mode(int(dp)))

    async def async_is_reachable(self) -> bool:
        """Whether the broker session is up.

        This says the pump is reachable *through Tuya*, which is the only
        sense in which it is reachable at all -- there is no local port
        answering to fall back on.
        """
        return self._transport.connected

    async def async_close(self) -> None:
        await self._transport.async_disconnect()

    async def async_refresh_all(self, *, need: tuple[str, ...] = ()) -> dict[str, Any]:
        """The cached picture, priming it on the first call.

        The pump reports state changes as they happen but volunteers nothing
        on connect, so the first refresh asks for what it is holding; after
        that the subscription keeps the cache current on its own.
        """
        if not self._primed:
            self._primed = True
            for dp in (DP_CUR_MODE, *need):
                try:
                    await self.async_request_dp(dp)
                except ZksjConnectionError:
                    raise
                except Exception as err:  # noqa: BLE001 - paho raises broadly
                    _LOGGER.debug("%s: DP %s request failed: %s", self.host, dp, err)
            # Give the pump a moment to answer before reporting an empty
            # picture that would blank every entity out.
            await asyncio.sleep(CLOUD_PRIME_DELAY)

        for dp in need:
            if dp in self._dps:
                continue
            try:
                await self.async_request_dp(dp)
            except ZksjConnectionError:
                raise
            except Exception as err:  # noqa: BLE001 - paho raises broadly
                _LOGGER.debug("%s: DP %s request failed: %s", self.host, dp, err)

        return dict(self._dps)


def _translate(dps: dict[str, Any]) -> dict[str, Any]:
    """Every value in a reply, translated from the wire's base64 to hex."""
    return {dp: from_wire(dp, value) for dp, value in dps.items()}


def _unwrap(result: Any, *, allow_empty: bool = False) -> dict[str, Any]:
    """Turn a tinytuya reply into a DP mapping, or raise.

    tinytuya reports failures in-band as a dict with an ``Error`` key rather
    than by raising, so an unchecked call silently looks like an empty pump.
    """
    if not isinstance(result, dict):
        raise ZksjConnectionError(f"unexpected reply from pump: {result!r}")

    if "Error" in result:
        error = result.get("Error")
        code = result.get("Err")
        payload = result.get("Payload")
        raise ZksjConnectionError(
            f"{error} (code {code})" + (f": {payload}" if payload else "")
        )

    dps = result.get("dps")
    if dps is None:
        if allow_empty:
            return {}
        raise ZksjConnectionError(f"pump replied without data points: {result!r}")
    return dict(dps)


async def async_probe(
    hass: HomeAssistant, *, host: str, device_id: str, local_key: str
) -> tuple[str, dict[str, Any]]:
    """Find which protocol version the pump answers on.

    Tuya firmware picks a version per product and never negotiates, so the
    only way to learn it is to try.  Returns the version and the first
    successful status.
    """
    last_error: Exception | None = None
    for version in PROTOCOL_VERSIONS:
        device = ZksjDevice(
            hass, host=host, device_id=device_id, local_key=local_key, version=version
        )
        try:
            dps = await device.async_status()
        except Exception as err:  # noqa: BLE001 - tinytuya raises broadly
            last_error = err
            _LOGGER.debug("%s: protocol %s did not answer: %s", host, version, err)
            continue
        finally:
            await device.async_close()
        if dps:
            return version, dps

    raise ZksjConnectionError(
        f"No Tuya protocol version got a reply from {host}. "
        "Check the IP, device id and local key."
    ) from last_error
