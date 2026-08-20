"""The ZKSJ AQUA integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .const import CONF_PROFILE
from .coordinator import ZksjConfigEntry, ZksjCoordinator
from .device import ZksjPump
from .protocol import ProtocolNotAvailable, get_profile

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ZksjConfigEntry) -> bool:
    """Set up a pump from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    try:
        profile = get_profile(entry.data[CONF_PROFILE])
    except ProtocolNotAvailable as err:
        # Not retryable: no amount of waiting produces a protocol profile.
        raise ConfigEntryError(str(err)) from err

    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Could not find ZKSJ pump {address}; it may be out of range"
        )

    pump = ZksjPump(ble_device, profile)
    coordinator = ZksjCoordinator(hass, entry, pump)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            lambda service_info, change: pump.set_ble_device(service_info.device),
            bluetooth.BluetoothCallbackMatcher(address=address),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZksjConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
