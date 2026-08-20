"""Feed mode."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZksjConfigEntry
from .entity import ZksjEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZksjConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ZksjFeedButton(entry.runtime_data)])


class ZksjFeedButton(ZksjEntity, ButtonEntity):
    """Starts feed mode, which the pump ends on its own timer."""

    _attr_translation_key = "feed"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "feed")

    async def async_press(self) -> None:
        await self.pump.async_set_feed(True)
