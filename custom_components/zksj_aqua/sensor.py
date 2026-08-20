"""Read-only pump telemetry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry
from .entity import ZksjEntity
from .protocol import PumpState


@dataclass(frozen=True, kw_only=True)
class ZksjSensorDescription(SensorEntityDescription):
    """A sensor and the state field behind it."""

    value_fn: Callable[[PumpState], int | None]


SENSORS: tuple[ZksjSensorDescription, ...] = (
    ZksjSensorDescription(
        key="rpm",
        translation_key="rpm",
        native_unit_of_measurement="RPM",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.rpm,
    ),
    ZksjSensorDescription(
        key="feed_remaining",
        translation_key="feed_remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state: state.feed_remaining,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    state = coordinator.data
    # Only surface what this pump actually reports; the field set differs
    # between generations and empty sensors are worse than absent ones.
    async_add_entities(
        ZksjSensor(coordinator, description)
        for description in SENSORS
        if description.value_fn(state) is not None
    )


class ZksjSensor(ZksjEntity, SensorEntity):
    """One telemetry field."""

    entity_description: ZksjSensorDescription

    def __init__(self, coordinator, description: ZksjSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        return self.entity_description.value_fn(self.pump_state)
