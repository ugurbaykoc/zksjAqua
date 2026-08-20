"""Config flow for ZKSJ AQUA."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers import config_validation as cv

from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_PROTOCOL_VERSION, DOMAIN
from .device import ZksjConnectionError, async_probe
from .discovery import DiscoveredDevice, async_discover

_LOGGER = logging.getLogger(__name__)

CONF_SELECTION = "selection"
MANUAL = "__manual__"

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_LOCAL_KEY): cv.string,
        vol.Optional(CONF_NAME, default="Wave pump"): cv.string,
    }
)

KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOCAL_KEY): cv.string,
        vol.Optional(CONF_NAME, default="Wave pump"): cv.string,
    }
)


class ZksjConfigFlow(ConfigFlow, domain=DOMAIN):
    """Find the pump on the network, then ask for the one thing we cannot find.

    Tuya devices broadcast their id and address, so discovery fills those in.
    The local key is not on the network at any point -- it lives in the Tuya
    account the pump was paired to -- so it has to be pasted in.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveredDevice] = {}
        self._chosen: DiscoveredDevice | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            selection = user_input[CONF_SELECTION]
            if selection == MANUAL:
                return await self.async_step_manual()
            self._chosen = self._discovered[selection]
            return await self.async_step_key()

        self._discovered = {
            device.device_id: device
            for device in await async_discover(self.hass)
            if device.device_id not in self._async_current_ids()
        }
        if not self._discovered:
            return await self.async_step_manual()

        choices = {
            device_id: device.label for device_id, device in self._discovered.items()
        }
        choices[MANUAL] = "Enter the address and device id myself"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_SELECTION): vol.In(choices)}),
        )

    async def async_step_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the local key of an already-located pump."""
        assert self._chosen is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            result = await self._async_create(
                host=self._chosen.host,
                device_id=self._chosen.device_id,
                local_key=user_input[CONF_LOCAL_KEY],
                name=user_input[CONF_NAME],
                errors=errors,
            )
            if result is not None:
                return result

        return self.async_show_form(
            step_id="key",
            data_schema=self.add_suggested_values_to_schema(KEY_SCHEMA, user_input),
            errors=errors,
            description_placeholders={
                "host": self._chosen.host,
                "device_id": self._chosen.device_id,
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Everything by hand, for a pump discovery could not hear."""
        errors: dict[str, str] = {}

        if user_input is not None:
            result = await self._async_create(
                host=user_input[CONF_HOST],
                device_id=user_input[CONF_DEVICE_ID],
                local_key=user_input[CONF_LOCAL_KEY],
                name=user_input[CONF_NAME],
                errors=errors,
            )
            if result is not None:
                return result

        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(MANUAL_SCHEMA, user_input),
            errors=errors,
        )

    async def _async_create(
        self,
        *,
        host: str,
        device_id: str,
        local_key: str,
        name: str,
        errors: dict[str, str],
    ) -> ConfigFlowResult | None:
        """Verify the credentials, then build the entry.

        Returns None when the caller should redisplay its form.
        """
        host, device_id, local_key = host.strip(), device_id.strip(), local_key.strip()

        await self.async_set_unique_id(device_id, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        try:
            version, dps = await async_probe(
                self.hass, host=host, device_id=device_id, local_key=local_key
            )
        except ZksjConnectionError as err:
            _LOGGER.debug("Probe of %s failed: %s", host, err)
            errors["base"] = "cannot_connect"
            return None
        except Exception:  # noqa: BLE001 - tinytuya raises broadly
            _LOGGER.exception("Unexpected error probing %s", host)
            errors["base"] = "unknown"
            return None

        _LOGGER.debug("%s answered on protocol %s with DPs %s", host, version, sorted(dps))
        return self.async_create_entry(
            title=name,
            data={
                CONF_HOST: host,
                CONF_DEVICE_ID: device_id,
                CONF_LOCAL_KEY: local_key,
                CONF_PROTOCOL_VERSION: version,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-point an entry, e.g. after the pump moved or was re-paired."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            local_key = user_input[CONF_LOCAL_KEY].strip()
            try:
                version, _ = await async_probe(
                    self.hass,
                    host=host,
                    device_id=entry.data[CONF_DEVICE_ID],
                    local_key=local_key,
                )
            except ZksjConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: host,
                        CONF_LOCAL_KEY: local_key,
                        CONF_PROTOCOL_VERSION: version,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): cv.string,
                    vol.Required(
                        CONF_LOCAL_KEY, default=entry.data[CONF_LOCAL_KEY]
                    ): cv.string,
                }
            ),
            errors=errors,
        )
