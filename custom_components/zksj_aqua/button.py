"""One-shot pump actions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_FEED_DURATION
from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .entity import ZksjEntity


@dataclass(frozen=True, kw_only=True)
class ZksjButtonDescription(ButtonEntityDescription):
    """A button and the coordinator call behind it."""

    press_fn: Callable[[ZksjCoordinator], Coroutine[Any, Any, None]]


BUTTONS: tuple[ZksjButtonDescription, ...] = (
    ZksjButtonDescription(
        key="feed",
        translation_key="feed",
        press_fn=lambda coordinator: coordinator.async_feed(DEFAULT_FEED_DURATION),
    ),
    ZksjButtonDescription(
        key="stop_feed",
        translation_key="stop_feed",
        press_fn=lambda coordinator: coordinator.async_stop_feed(),
    ),
    ZksjButtonDescription(
        key="sync_time",
        translation_key="sync_time",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_sync_time(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ZksjButton(coordinator, description) for description in BUTTONS
    )


class ZksjButton(ZksjEntity, ButtonEntity):
    """One pump action."""

    entity_description: ZksjButtonDescription

    def __init__(
        self, coordinator: ZksjCoordinator, description: ZksjButtonDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
