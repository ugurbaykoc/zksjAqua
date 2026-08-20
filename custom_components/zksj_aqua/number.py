"""Flow level."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry
from .entity import ZksjEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ZksjSpeedNumber(entry.runtime_data)])


class ZksjSpeedNumber(ZksjEntity, NumberEntity):
    """The pump's flow level, on the vendor's own 1-N scale.

    Deliberately not a percentage: the pump only accepts discrete steps, and
    rounding a percentage onto them makes the UI disagree with the hardware.
    """

    _attr_translation_key = "speed"
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "speed")
        self._attr_native_min_value = coordinator.pump.profile.min_speed
        self._attr_native_max_value = coordinator.pump.profile.max_speed

    @property
    def native_value(self) -> float | None:
        speed = self.pump_state.speed
        return None if speed is None else float(speed)

    async def async_set_native_value(self, value: float) -> None:
        await self.pump.async_set_speed(int(value))
