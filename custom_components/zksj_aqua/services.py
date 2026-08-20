"""Services for things that do not fit an entity.

Entities cover the running segment; these cover the whole program and the
parameters the buttons have to assume.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN
from .coordinator import ZksjCoordinator
from .protocol import LIMITS, SECONDS_PER_DAY, WaveSegment, WaveType, clamp

ATTR_DEVICE_ID = "device_id"
ATTR_DURATION = "duration"
ATTR_POWER = "power"
ATTR_SEGMENTS = "segments"

SERVICE_FEED = "feed"
SERVICE_SET_PROGRAM = "set_program"
SERVICE_SYNC_TIME = "sync_time"

_WAVE_TYPES = {wave_type.name.lower(): wave_type for wave_type in WaveType}

SEGMENT_SCHEMA = vol.Schema(
    {
        vol.Required("type"): vol.In(sorted(_WAVE_TYPES)),
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Optional("min_power", default=0): vol.All(int, vol.Range(0, 100)),
        vol.Optional("max_power", default=100): vol.All(int, vol.Range(0, 100)),
        vol.Optional("freq", default=0): vol.All(int, vol.Range(0, 1000)),
        vol.Optional("pwm", default=0): vol.All(int, vol.Range(0, 100)),
    }
)

FEED_SCHEMA = vol.Schema(
    {
        # A "target" in services.yaml arrives as a list, even for one device.
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_DURATION): vol.All(int, vol.Range(1, 86400)),
        vol.Optional(ATTR_POWER, default=0): vol.All(int, vol.Range(0, 100)),
    }
)

SET_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SEGMENTS): vol.All(
            cv.ensure_list, vol.Length(min=1), [SEGMENT_SCHEMA]
        ),
    }
)

SYNC_TIME_SCHEMA = vol.Schema(
    {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_FEED):
        return

    async def handle_feed(call: ServiceCall) -> None:
        for coordinator in _resolve_all(hass, call):
            await coordinator.async_feed(
                call.data[ATTR_DURATION], call.data.get(ATTR_POWER, 0)
            )

    async def handle_set_program(call: ServiceCall) -> None:
        # Build once: a malformed segment should fail before any pump is
        # left holding half a program.
        segments = [_build_segment(raw) for raw in call.data[ATTR_SEGMENTS]]
        for coordinator in _resolve_all(hass, call):
            await coordinator.async_set_program(segments)

    async def handle_sync_time(call: ServiceCall) -> None:
        for coordinator in _resolve_all(hass, call):
            await coordinator.async_sync_time()

    hass.services.async_register(DOMAIN, SERVICE_FEED, handle_feed, FEED_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROGRAM, handle_set_program, SET_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_TIME, handle_sync_time, SYNC_TIME_SCHEMA
    )


def _build_segment(raw: dict) -> WaveSegment:
    """Turn one service-call segment into a wire segment.

    Values are clamped to what the waveform accepts rather than rejected, so
    a program written for one wave type can be retyped without the caller
    having to know every range by heart.
    """
    wave_type = _WAVE_TYPES[raw["type"]]
    limits = LIMITS[wave_type]
    start, end = _seconds(raw["start"]), _seconds(raw["end"])
    if start == end:
        raise ServiceValidationError(
            f"Segment start and end are both {raw['start']}; a segment needs "
            "a non-zero span."
        )
    return WaveSegment(
        type=wave_type,
        min_power=clamp(raw["min_power"], limits.power),
        max_power=clamp(raw["max_power"], limits.power),
        freq=clamp(raw["freq"], limits.freq) if limits.has_freq else 0,
        pwm=clamp(raw["pwm"], limits.pwm) if limits.has_pwm else 0,
        start_time=start,
        # Midnight as an end time means the end of the day, not the start.
        end_time=SECONDS_PER_DAY if end == 0 else end,
    )


def _seconds(value) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _resolve_all(hass: HomeAssistant, call: ServiceCall) -> list[ZksjCoordinator]:
    """Every pump the call targets."""
    return [_resolve(hass, device_id) for device_id in call.data[ATTR_DEVICE_ID]]


def _resolve(hass: HomeAssistant, device_id: str) -> ZksjCoordinator:
    """Find the coordinator behind a device registry id."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"No device with id {device_id}")

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN:
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is not None:
                return coordinator
            raise ServiceValidationError(
                f"{device.name or device_id} is not loaded right now."
            )

    raise ServiceValidationError(f"{device.name or device_id} is not a ZKSJ pump.")
