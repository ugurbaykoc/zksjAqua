"""The ZKSJ AQUA integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_PROTOCOL_VERSION
from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .device import ZksjDevice
from .services import async_setup_services

# Reachability needs no credentials, so it is always available.
MONITOR_PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]

# Everything else needs the local key to read or write data points.
CONTROL_PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def _platforms(coordinator: ZksjCoordinator) -> list[Platform]:
    if coordinator.monitor_only:
        return MONITOR_PLATFORMS
    return MONITOR_PLATFORMS + CONTROL_PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ZksjConfigEntry) -> bool:
    """Set up a pump from a config entry."""
    device = ZksjDevice(
        hass,
        host=entry.data[CONF_HOST],
        device_id=entry.data[CONF_DEVICE_ID],
        local_key=entry.data[CONF_LOCAL_KEY],
        version=entry.data[CONF_PROTOCOL_VERSION],
    )

    coordinator = ZksjCoordinator(hass, entry, device)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    if not coordinator.monitor_only:
        async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, _platforms(coordinator))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZksjConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry.runtime_data)
    )
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
