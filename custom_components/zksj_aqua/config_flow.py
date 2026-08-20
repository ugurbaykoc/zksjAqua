"""Config flow for ZKSJ AQUA."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import CONF_PROFILE, DOMAIN
from .protocol import PROFILES, DeviceProfile, match_profile


class ZksjConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adds a pump, either from a BLE advertisement or by picking one."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, tuple[str, DeviceProfile]] = {}
        self._pending: tuple[str, str, DeviceProfile] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a pump Home Assistant spotted on its own."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        profile = match_profile(discovery_info.name)
        if profile is None:
            return self.async_abort(reason="unsupported_model")

        self._pending = (discovery_info.address, discovery_info.name, profile)
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before adding a discovered pump."""
        assert self._pending is not None
        address, name, profile = self._pending

        if user_input is not None:
            return self.async_create_entry(
                title=name,
                data={CONF_ADDRESS: address, CONF_PROFILE: profile.key},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": name, "model": profile.model},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a pump from the ones currently advertising."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            name, profile = self._discovered[address]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=name,
                data={CONF_ADDRESS: address, CONF_PROFILE: profile.key},
            )

        if not PROFILES:
            return self.async_abort(reason="no_protocol")

        configured = self._async_current_ids()
        self._discovered = {}
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured:
                continue
            profile = match_profile(info.name)
            if profile is not None:
                self._discovered[info.address] = (info.name, profile)

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{name} ({address})"
                            for address, (name, _) in self._discovered.items()
                        }
                    )
                }
            ),
        )
