"""Keeps entity state in step with the pump."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL
from .device import ZksjConnectionError, ZksjPump
from .protocol import PumpState

_LOGGER = logging.getLogger(__name__)

# Plain alias rather than a 3.12 `type` statement so the component still
# imports on older runtimes.
ZksjConfigEntry = ConfigEntry["ZksjCoordinator"]


class ZksjCoordinator(DataUpdateCoordinator[PumpState]):
    """Polls the pump, and republishes whatever it volunteers in between.

    The pump pushes a notification after every command -- including commands
    sent from the vendor app or the pump's own buttons while we happen to be
    connected -- so the poll is only a backstop for changes made while we were
    disconnected.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ZksjConfigEntry, pump: ZksjPump
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {pump.name}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.pump = pump
        self._unsubscribe = pump.add_listener(self._handle_push)

    @callback
    def _handle_push(self, state: PumpState) -> None:
        self.async_set_updated_data(state)

    async def _async_update_data(self) -> PumpState:
        try:
            return await self.pump.async_refresh()
        except ZksjConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def async_shutdown(self) -> None:
        self._unsubscribe()
        await super().async_shutdown()
        await self.pump.async_disconnect()
