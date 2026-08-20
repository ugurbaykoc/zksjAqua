"""Config flow for ZKSJ AQUA."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
)
from .device import ZksjConnectionError, async_probe

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_LOCAL_KEY): cv.string,
        vol.Optional(CONF_NAME, default="Wave pump"): cv.string,
    }
)


class ZksjConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the pump's address and local credentials."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()
            local_key = user_input[CONF_LOCAL_KEY].strip()
            host = user_input[CONF_HOST].strip()

            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

            try:
                version, dps = await async_probe(
                    self.hass, host=host, device_id=device_id, local_key=local_key
                )
            except ZksjConnectionError as err:
                _LOGGER.debug("Probe of %s failed: %s", host, err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - tinytuya raises broadly
                _LOGGER.exception("Unexpected error probing %s", host)
                errors["base"] = "unknown"
            else:
                _LOGGER.debug(
                    "%s answered on protocol %s with DPs %s",
                    host,
                    version,
                    sorted(dps),
                )
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_HOST: host,
                        CONF_DEVICE_ID: device_id,
                        CONF_LOCAL_KEY: local_key,
                        CONF_PROTOCOL_VERSION: version,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-point an existing entry, e.g. after the pump changed address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            device_id = entry.data[CONF_DEVICE_ID]
            local_key = user_input[CONF_LOCAL_KEY].strip()
            try:
                version, _ = await async_probe(
                    self.hass, host=host, device_id=device_id, local_key=local_key
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
