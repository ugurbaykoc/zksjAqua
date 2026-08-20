"""Pump on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry
from .entity import ZksjEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ZksjPowerSwitch(entry.runtime_data)])


class ZksjPowerSwitch(ZksjEntity, SwitchEntity):
    """Starts and stops the pump."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "power"
    _attr_name = None

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool | None:
        return self.pump_state.power

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.pump.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.pump.async_set_power(False)
