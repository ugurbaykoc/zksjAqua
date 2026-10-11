"""Polls the pump and turns its data points into usable state."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .device import ZksjCloudDevice, ZksjConnectionError, ZksjDevice
from .protocol import (
    COMMON_QUERY_CLOCK,
    COMMON_SYNC_CLOCK,
    DP_COMMON,
    DP_CUR_MODE,
    DP_CUR_POWER,
    DP_FEED,
    DP_SWITCH,
    DP_SYNC_TIME,
    SECONDS_PER_DAY,
    FeedState,
    WaveSegment,
    WaveType,
    ZksjProtocolError,
    active_segment_index,
    clamp,
    decode_clock,
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

    reachable: bool = False
    on: bool | None = None
    power: int | None = None
    program: list[WaveSegment] = field(default_factory=list)
    feed: FeedState | None = None
    # The pump's own calendar clock (DP 122), and how far behind local time
    # it was when it last reported -- positive means the pump is slow.
    clock: datetime | None = None
    clock_drift: int | None = None

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
        self,
        hass: HomeAssistant,
        entry: ZksjConfigEntry,
        device: ZksjDevice | ZksjCloudDevice,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.host}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )
        self.device = device
        self._clock_requested = False
        if isinstance(device, ZksjCloudDevice):
            # Cloud state is pushed, not polled: take each report as it
            # lands rather than making entities wait for the next tick.
            device.on_update = self._handle_pushed_dps

    @callback
    def _handle_pushed_dps(self, dps: dict[str, Any]) -> None:
        """A state report arrived from the broker between polls."""
        state = self._parse(dps)
        state.reachable = True
        self.async_set_updated_data(state)

    # -- reading ----------------------------------------------------------

    @property
    def monitor_only(self) -> bool:
        """True when we have no local key, so only reachability is knowable."""
        return not self.device.has_local_key

    async def _async_update_data(self) -> ZksjState:
        if self.monitor_only:
            # Never fail the update here: "the pump is unreachable" is the
            # answer this mode exists to report, not an error that should
            # blank the entity out.
            reachable = await self.device.async_is_reachable()
            return ZksjState(reachable=reachable)

        need: tuple[str, ...] = ()
        if self.data is None or not self.data.program:
            # The program changes only when we write it, so once a poll
            # has it there is nothing to gain from asking again -- only
            # another round trip for a link that already drops the odd
            # packet.
            need = (DP_CUR_MODE,)

        try:
            dps = await self.device.async_refresh_all(need=need)
        except ZksjConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - tinytuya raises broadly
            raise UpdateFailed(f"{self.device.host}: {err}") from err
        state = self._parse(dps)
        state.reachable = True
        if not self._clock_requested:
            self._clock_requested = True
            await self._async_prime_clock()
        return state

    async def _async_prime_clock(self) -> None:
        """Ask once for the pump's calendar clock (DP 122).

        The pump never volunteers it; the app asks on every page it opens.
        This deliberately writes straight to the transport instead of going
        through :meth:`_write`, which would schedule a refresh from inside
        the refresh we are already in.  It is also allowed to fail quietly:
        DP 122 is a channel the app shares across ZKSJ products, and a pump
        that does not answer it should cost us one sensor, not the device.
        """
        try:
            await self.device.async_set(DP_COMMON, COMMON_QUERY_CLOCK)
        except Exception as err:  # noqa: BLE001 - transports raise broadly
            _LOGGER.debug("%s: clock request failed: %s", self.device.host, err)

    def _parse(self, dps: dict[str, Any]) -> ZksjState:
        """Decode a DP mapping, keeping whatever still parses.

        A pump that reports one malformed blob should cost us that one
        entity, not the whole device, so each field is decoded on its own.
        """
        previous = self.data or ZksjState()
        state = ZksjState(
            reachable=True,
            on=previous.on,
            power=previous.power,
            program=previous.program,
            clock=previous.clock,
            clock_drift=previous.clock_drift,
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

        if DP_COMMON in dps:
            # The same channel carries the short commands we write, so a
            # payload that is not a clock report is ordinary traffic rather
            # than a fault -- hence debug, not a warning.
            try:
                reported = decode_clock(dps[DP_COMMON])
            except ZksjProtocolError as err:
                _LOGGER.debug("%s: %s", self.device.host, err)
            else:
                state.clock = reported.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                state.clock_drift = round((dt_util.now() - state.clock).total_seconds())

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

    async def async_request_clock(self) -> None:
        """Ask the pump to report its calendar clock."""
        await self._write(DP_COMMON, COMMON_QUERY_CLOCK)

    async def async_sync_clock(self) -> None:
        """Have the pump re-calibrate its calendar clock, then re-read it.

        DP 107 only carries a time of day, so it leaves the pump's date
        alone; this is the command the app uses when the whole clock has
        drifted.  The pump answers on its own schedule, so the follow-up
        request is what gets the sensor to show the result.
        """
        await self._write(DP_COMMON, COMMON_SYNC_CLOCK)
        await self.async_request_clock()

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
        active = state.program[index]
        if active.start_time == active.end_time:
            # A zero-length window is what a freshly cleared program looks
            # like on this pump -- not a real schedule slot. Editing it
            # should claim the whole day; writing back a slice that spans
            # nothing is what a real pump rejected with a garbled reply.
            active = WaveSegment(
                type=active.type,
                identity=active.identity,
                min_power=active.min_power,
                max_power=active.max_power,
                freq=active.freq,
                pwm=active.pwm,
                start_time=0,
                end_time=SECONDS_PER_DAY,
            )
        program = replace_segment(state.program, index, edit(active))
        await self._write(DP_CUR_MODE, encode_program(program))

    async def _write(self, dp: str, value: Any) -> None:
        try:
            result = await self.device.async_set(dp, value)
        except ZksjConnectionError as err:
            raise HomeAssistantError(str(err)) from err
        except Exception as err:  # noqa: BLE001 - tinytuya raises broadly
            raise HomeAssistantError(f"{self.device.host}: {err}") from err
        if result:
            # The pump often echoes the new value straight back in its ack.
            # Use it now rather than waiting on the next poll -- which, by
            # design, no longer re-requests DP 101 on its own once it has
            # been seen, so nothing would otherwise refresh it.
            self.async_set_updated_data(self._parse(result))
        else:
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
