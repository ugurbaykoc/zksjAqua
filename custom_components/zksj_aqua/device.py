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
from typing import Any

import tinytuya
from homeassistant.core import HomeAssistant

from .const import CONNECTION_TIMEOUT, PROTOCOL_VERSIONS, TUYA_LOCAL_PORT
from .protocol import (
    DP_CUR_MODE,
    DP_CUR_POWER,
    DP_FEED,
    DP_GET_MODE,
    DP_SWITCH,
    encode_get_mode,
)

_LOGGER = logging.getLogger(__name__)

# DPs worth having before the first entity renders.
_ESSENTIAL_DPS = (DP_SWITCH, DP_CUR_POWER, DP_CUR_MODE)


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
        return _unwrap(result)

    def _set(self, dp: str, value: Any) -> dict[str, Any]:
        device = self._ensure()
        result = device.set_value(dp, value)
        # A set can come back empty; that is not an error, just no echo.
        if not result:
            return {}
        return _unwrap(result, allow_empty=True)

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

    async def async_refresh_all(self) -> dict[str, Any]:
        """Status, plus a nudge for anything important the pump left out."""
        dps = await self.async_status()
        missing = [dp for dp in _ESSENTIAL_DPS if dp not in dps]
        for dp in missing:
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
