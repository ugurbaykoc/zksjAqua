"""Wave pattern."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry
from .entity import ZksjEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ZksjModeSelect(entry.runtime_data)])


class ZksjModeSelect(ZksjEntity, SelectEntity):
    """Switches between the pump's wave patterns."""

    _attr_translation_key = "wave_mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "wave_mode")
        self._attr_options = [mode.key for mode in coordinator.pump.profile.modes]

    @property
    def current_option(self) -> str | None:
        return self.pump_state.mode

    async def async_select_option(self, option: str) -> None:
        await self.pump.async_set_mode(option)
