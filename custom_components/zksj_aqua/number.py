"""The tunable fields of the segment currently running."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .entity import ZksjEntity
from .protocol import WaveLimits, WaveSegment


@dataclass(frozen=True, kw_only=True)
class ZksjNumberDescription(NumberEntityDescription):
    """A segment field, and how to find its value and range."""

    field: str
    value_fn: Callable[[WaveSegment], int]
    bounds_fn: Callable[[WaveLimits], tuple[int, int]]
    # Frequency and duty cycle exist only for the two modulated waveforms.
    supported_fn: Callable[[WaveLimits], bool] = lambda limits: True


NUMBERS: tuple[ZksjNumberDescription, ...] = (
    ZksjNumberDescription(
        key="min_power",
        translation_key="min_power",
        field="min_power",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda segment: segment.min_power,
        bounds_fn=lambda limits: limits.power,
    ),
    ZksjNumberDescription(
        key="max_power",
        translation_key="max_power",
        field="max_power",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda segment: segment.max_power,
        bounds_fn=lambda limits: limits.power,
    ),
    ZksjNumberDescription(
        key="frequency",
        translation_key="frequency",
        field="freq",
        value_fn=lambda segment: segment.freq,
        bounds_fn=lambda limits: limits.freq,
        supported_fn=lambda limits: limits.has_freq,
    ),
    ZksjNumberDescription(
        key="pwm",
        translation_key="pwm",
        field="pwm",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda segment: segment.pwm,
        bounds_fn=lambda limits: limits.pwm,
        supported_fn=lambda limits: limits.has_pwm,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ZksjSegmentNumber(coordinator, description) for description in NUMBERS
    )


class ZksjSegmentNumber(ZksjEntity, NumberEntity):
    """One numeric field of the running segment.

    The range is not fixed: each waveform accepts its own, so the slider
    re-scales when the wave mode changes and disappears for waveforms where
    the field means nothing.
    """

    entity_description: ZksjNumberDescription
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(
        self, coordinator: ZksjCoordinator, description: ZksjNumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        segment = self.active_segment
        if segment is None:
            return False
        return super().available and self.entity_description.supported_fn(
            segment.limits
        )

    @property
    def native_min_value(self) -> float:
        return float(self._bounds[0])

    @property
    def native_max_value(self) -> float:
        return float(self._bounds[1])

    @property
    def native_value(self) -> float | None:
        segment = self.active_segment
        if segment is None:
            return None
        return float(self.entity_description.value_fn(segment))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_field(
            self.entity_description.field, int(value)
        )

    @property
    def _bounds(self) -> tuple[int, int]:
        segment = self.active_segment
        if segment is None:
            return (0, 100)
        return self.entity_description.bounds_fn(segment.limits)
