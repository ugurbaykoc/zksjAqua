"""Read-only pump telemetry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry, ZksjCoordinator, ZksjState
from .entity import ZksjEntity


@dataclass(frozen=True, kw_only=True)
class ZksjSensorDescription(SensorEntityDescription):
    """A sensor and the state field behind it."""

    value_fn: Callable[[ZksjState], int | datetime | None]


SENSORS: tuple[ZksjSensorDescription, ...] = (
    ZksjSensorDescription(
        key="feed_countdown",
        translation_key="feed_countdown",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state: state.feed.countdown if state.feed else None,
    ),
    ZksjSensorDescription(
        key="program_segments",
        translation_key="program_segments",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: len(state.program) or None,
    ),
    # The pump's own clock, which it keeps to run time-based programs. It
    # stays unknown on a pump that does not answer DP 122 -- a channel the
    # app shares across ZKSJ products, so not every one of them will.
    ZksjSensorDescription(
        key="pump_clock",
        translation_key="pump_clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.clock,
    ),
    ZksjSensorDescription(
        key="clock_drift",
        translation_key="clock_drift",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.clock_drift,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ZksjSensor(coordinator, description) for description in SENSORS
    )


class ZksjSensor(ZksjEntity, SensorEntity):
    """One telemetry field."""

    entity_description: ZksjSensorDescription

    def __init__(
        self, coordinator: ZksjCoordinator, description: ZksjSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | datetime | None:
        return self.entity_description.value_fn(self.pump_state)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the running segment's window on the program sensor.

        Knowing *which* stretch of the day is live is what makes a
        multi-segment program legible from the dashboard.
        """
        if self.entity_description.key != "program_segments":
            return None
        segment = self.active_segment
        if segment is None:
            return None
        return {
            "active_start": _clock(segment.start_time),
            "active_end": _clock(segment.end_time),
            "active_type": segment.type.name.lower(),
        }


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
