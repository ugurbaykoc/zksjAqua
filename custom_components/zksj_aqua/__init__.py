"""The ZKSJ AQUA integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MQTT_BROKER,
    CONF_MQTT_CLIENT_ID,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_SOURCE_ID,
    CONF_MQTT_USERNAME,
    CONF_PROTOCOL_VERSION,
    CONF_TRANSPORT,
    DEFAULT_MQTT_BROKER,
    DEFAULT_MQTT_PORT,
    TRANSPORT_CLOUD,
)
from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .device import ZksjCloudDevice, ZksjDevice
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


async def _async_build_device(
    hass: HomeAssistant, entry: ZksjConfigEntry
) -> ZksjDevice | ZksjCloudDevice:
    """The transport this entry was set up for.

    Cloud entries are the ones that work on a pump that ignores local
    control; the local transport stays for pumps (or firmware) that answer
    it, and for monitor-only entries with no key at all.
    """
    if entry.data.get(CONF_TRANSPORT) != TRANSPORT_CLOUD:
        return ZksjDevice(
            hass,
            host=entry.data[CONF_HOST],
            device_id=entry.data[CONF_DEVICE_ID],
            local_key=entry.data[CONF_LOCAL_KEY],
            version=entry.data[CONF_PROTOCOL_VERSION],
        )

    device = ZksjCloudDevice(
        hass,
        device_id=entry.data[CONF_DEVICE_ID],
        local_key=entry.data[CONF_LOCAL_KEY],
        broker=entry.data.get(CONF_MQTT_BROKER, DEFAULT_MQTT_BROKER),
        port=entry.data.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT),
        client_id=entry.data[CONF_MQTT_CLIENT_ID],
        username=entry.data[CONF_MQTT_USERNAME],
        password=entry.data[CONF_MQTT_PASSWORD],
        source_id=entry.data[CONF_MQTT_SOURCE_ID],
    )
    await device.async_connect()
    return device


async def async_setup_entry(hass: HomeAssistant, entry: ZksjConfigEntry) -> bool:
    """Set up a pump from a config entry."""
    device = await _async_build_device(hass, entry)

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
