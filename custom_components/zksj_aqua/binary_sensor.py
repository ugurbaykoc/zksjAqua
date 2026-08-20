"""Whether the pump is powered up and on the network."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .entity import ZksjEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ZksjConnectivitySensor(entry.runtime_data)])


class ZksjConnectivitySensor(ZksjEntity, BinarySensorEntity):
    """Reports the pump answering on its local control port.

    This is the one thing knowable without the pump's local key, so it is
    also the only entity a monitor-only entry creates. It means the pump has
    power and is on the network -- not that it is circulating: a pump
    switched off over its own data point still answers here.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "reachable"

    def __init__(self, coordinator: ZksjCoordinator) -> None:
        super().__init__(coordinator, "reachable")

    @property
    def available(self) -> bool:
        # In monitor-only mode the update never fails, and "not reachable" is
        # a real reading rather than a gap, so the entity always has an
        # answer to give.
        if self.coordinator.monitor_only:
            return True
        return super().available

    @property
    def is_on(self) -> bool:
        return self.pump_state.reachable
