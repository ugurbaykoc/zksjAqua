"""Polls the pump and turns its data points into usable state."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .device import ZksjConnectionError, ZksjDevice
from .protocol import (
    DP_CUR_MODE,
    DP_CUR_POWER,
    DP_FEED,
    DP_SWITCH,
    DP_SYNC_TIME,
    FeedState,
    WaveSegment,
    WaveType,
    ZksjProtocolError,
    active_segment_index,
    clamp,
    decode_feed,
    decode_power,
    decode_program,
    encode_feed,
    encode_program,
    encode_sync_time,
    replace_segment,
    retyped,
)

_LOGGER = logging.getLogger(__name__)

ZksjConfigEntry = ConfigEntry["ZksjCoordinator"]


@dataclass(slots=True)
class ZksjState:
    """The pump as of the last successful read."""

    on: bool | None = None
    power: int | None = None
    program: list[WaveSegment] = field(default_factory=list)
    feed: FeedState | None = None

    def active_index(self, seconds_of_day: int) -> int | None:
        if not self.program:
            return None
        return active_segment_index(self.program, seconds_of_day)

    def active_segment(self, seconds_of_day: int) -> WaveSegment | None:
        index = self.active_index(seconds_of_day)
        return None if index is None else self.program[index]


class ZksjCoordinator(DataUpdateCoordinator[ZksjState]):
    """Keeps :class:`ZksjState` current and applies edits to the pump."""

    def __init__(
        self, hass: HomeAssistant, entry: ZksjConfigEntry, device: ZksjDevice
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.host}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )
        self.device = device

    # -- reading ----------------------------------------------------------

    async def _async_update_data(self) -> ZksjState:
        try:
            dps = await self.device.async_refresh_all()
        except ZksjConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - tinytuya raises broadly
            raise UpdateFailed(f"{self.device.host}: {err}") from err
        return self._parse(dps)

    def _parse(self, dps: dict[str, Any]) -> ZksjState:
        """Decode a DP mapping, keeping whatever still parses.

        A pump that reports one malformed blob should cost us that one
        entity, not the whole device, so each field is decoded on its own.
        """
        previous = self.data or ZksjState()
        state = ZksjState(
            on=previous.on, power=previous.power, program=previous.program
        )

        if DP_SWITCH in dps:
            state.on = bool(dps[DP_SWITCH])

        if DP_CUR_POWER in dps:
            try:
                state.power = decode_power(dps[DP_CUR_POWER])
            except ZksjProtocolError as err:
                _LOGGER.debug("%s: %s", self.device.host, err)

        if DP_CUR_MODE in dps:
            try:
                state.program = decode_program(dps[DP_CUR_MODE])
            except ZksjProtocolError as err:
                _LOGGER.debug("%s: %s", self.device.host, err)

        if DP_FEED in dps:
            try:
                state.feed = decode_feed(dps[DP_FEED])
            except ZksjProtocolError as err:
                _LOGGER.debug("%s: %s", self.device.host, err)

        return state

    @property
    def seconds_of_day(self) -> int:
        """Local time as the pump counts it, for locating the live segment."""
        now = dt_util.now()
        return now.hour * 3600 + now.minute * 60 + now.second

    @property
    def active_segment(self) -> WaveSegment | None:
        if self.data is None:
            return None
        return self.data.active_segment(self.seconds_of_day)

    # -- writing ----------------------------------------------------------

    async def async_set_switch(self, on: bool) -> None:
        await self._write(DP_SWITCH, on)

    async def async_set_wave_type(self, wave_type: WaveType) -> None:
        """Recast the running segment, leaving the rest of the program alone."""
        await self._edit_active(lambda segment: retyped(segment, wave_type))

    async def async_set_field(self, field_name: str, value: int) -> None:
        """Set one numeric field of the running segment, clamped to its type."""

        def edit(segment: WaveSegment) -> WaveSegment:
            limits = segment.limits
            bounds = {
                "min_power": limits.power,
                "max_power": limits.power,
                "freq": limits.freq,
                "pwm": limits.pwm,
            }[field_name]
            updated = WaveSegment(**{**_as_dict(segment), field_name: clamp(value, bounds)})
            # The pump treats min as a floor and max as a ceiling; letting
            # them cross would ask it for an empty range.
            if updated.min_power > updated.max_power:
                if field_name == "min_power":
                    updated.max_power = updated.min_power
                else:
                    updated.min_power = updated.max_power
            return updated

        await self._edit_active(edit)

    async def async_feed(self, duration: int, power: int = 0) -> None:
        """Start feed mode; the pump runs the countdown itself."""
        await self._write(DP_FEED, encode_feed(start=True, duration=duration, power=power))

    async def async_stop_feed(self) -> None:
        await self._write(DP_FEED, encode_feed(start=False, duration=0))

    async def async_sync_time(self) -> None:
        """Push local time, so time-based programs run when they should."""
        await self._write(DP_SYNC_TIME, encode_sync_time(self.seconds_of_day))

    async def async_set_program(self, segments: list[WaveSegment]) -> None:
        await self._write(DP_CUR_MODE, encode_program(segments))

    async def _edit_active(self, edit) -> None:
        state = self.data
        if state is None or not state.program:
            raise HomeAssistantError(
                "The pump has not reported its program yet, so there is "
                "nothing to change."
            )
        index = active_segment_index(state.program, self.seconds_of_day)
        program = replace_segment(state.program, index, edit(state.program[index]))
        await self._write(DP_CUR_MODE, encode_program(program))

    async def _write(self, dp: str, value: Any) -> None:
        try:
            await self.device.async_set(dp, value)
        except ZksjConnectionError as err:
            raise HomeAssistantError(str(err)) from err
        except Exception as err:  # noqa: BLE001 - tinytuya raises broadly
            raise HomeAssistantError(f"{self.device.host}: {err}") from err
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.device.async_close()


def _as_dict(segment: WaveSegment) -> dict[str, Any]:
    return {
        "type": segment.type,
        "identity": segment.identity,
        "min_power": segment.min_power,
        "max_power": segment.max_power,
        "freq": segment.freq,
        "pwm": segment.pwm,
        "start_time": segment.start_time,
        "end_time": segment.end_time,
    }
