"""Shared entity base for ZKSJ pumps."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ZksjCoordinator, ZksjState
from .protocol import WaveSegment


class ZksjEntity(CoordinatorEntity[ZksjCoordinator]):
    """An entity backed by one pump."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZksjCoordinator, key: str) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.config_entry.title,
            configuration_url=f"http://{device.host}",
        )

    @property
    def pump_state(self) -> ZksjState:
        return self.coordinator.data

    @property
    def active_segment(self) -> WaveSegment | None:
        return self.coordinator.active_segment
