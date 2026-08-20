"""Wave pattern of the segment currently running."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .entity import ZksjEntity
from .protocol import WaveType

# Stable option strings; the wire values live in WaveType.
OPTIONS: dict[str, WaveType] = {
    "constant": WaveType.CONSTANT,
    "pulse": WaveType.PULSE,
    "gyre": WaveType.GYRE,
    "nutrient_transport": WaveType.NUTRIENT_TRANSPORT,
    "tidal_swell": WaveType.TIDAL_SWELL,
    "random": WaveType.RANDOM,
}
BY_TYPE: dict[WaveType, str] = {value: key for key, value in OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ZksjWaveModeSelect(entry.runtime_data)])


class ZksjWaveModeSelect(ZksjEntity, SelectEntity):
    """Recasts the running segment as another waveform.

    A pump's program can be several segments across the day; this changes
    only the one playing now, so a daily schedule survives being nudged.
    """

    _attr_translation_key = "wave_mode"
    _attr_options = list(OPTIONS)

    def __init__(self, coordinator: ZksjCoordinator) -> None:
        super().__init__(coordinator, "wave_mode")

    @property
    def available(self) -> bool:
        return super().available and self.active_segment is not None

    @property
    def current_option(self) -> str | None:
        segment = self.active_segment
        return None if segment is None else BY_TYPE.get(segment.type)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_wave_type(OPTIONS[option])
