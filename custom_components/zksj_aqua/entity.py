"""Shared entity base for ZKSJ pumps."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZksjCoordinator
from .protocol import PumpState


class ZksjEntity(CoordinatorEntity[ZksjCoordinator]):
    """An entity backed by one pump."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZksjCoordinator, key: str) -> None:
        super().__init__(coordinator)
        pump = coordinator.pump
        self._attr_unique_id = f"{pump.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pump.address)},
            connections={(CONNECTION_BLUETOOTH, pump.address)},
            manufacturer=MANUFACTURER,
            model=pump.profile.model,
            name=pump.name,
        )

    @property
    def pump(self):
        return self.coordinator.pump

    @property
    def pump_state(self) -> PumpState:
        return self.coordinator.data
