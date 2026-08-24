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
    DEFAULT_PROTOCOL_VERSION,
    DOMAIN,
    TRANSPORT_CLOUD,
    TRANSPORT_LOCAL,
)
from .device import ZksjCloudDevice, ZksjConnectionError, async_probe
from .discovery import DiscoveredDevice, async_discover

_LOGGER = logging.getLogger(__name__)

CONF_SELECTION = "selection"
MANUAL = "__manual__"

CLOUD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_LOCAL_KEY): cv.string,
        vol.Required(CONF_MQTT_USERNAME): cv.string,
        vol.Required(CONF_MQTT_PASSWORD): cv.string,
        vol.Required(CONF_MQTT_CLIENT_ID): cv.string,
        vol.Required(CONF_MQTT_SOURCE_ID): cv.positive_int,
        vol.Optional(CONF_MQTT_BROKER, default=DEFAULT_MQTT_BROKER): cv.string,
        vol.Optional(CONF_MQTT_PORT, default=DEFAULT_MQTT_PORT): cv.port,
        vol.Optional(CONF_NAME, default="Wave pump"): cv.string,
    }
)

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Optional(CONF_LOCAL_KEY, default=""): cv.string,
        vol.Optional(CONF_NAME, default="Wave pump"): cv.string,
    }
)

KEY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_LOCAL_KEY, default=""): cv.string,
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
        """Pick a transport before anything else.

        Which one a pump needs is not something we can probe for: a pump
        that ignores local control looks exactly like a wrong local key, so
        asking is more honest than guessing.
        """
        return self.async_show_menu(
            step_id="user",
            menu_options=["cloud", "local"],
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a pump over Tuya's MQTT broker.

        Everything asked for here comes out of a capture of the vendor app's
        own session -- see docs/ZKSJ.md. The credentials are session-scoped
        and expire; Reconfigure is how they get replaced.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()
            await self.async_set_unique_id(device_id, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            data = {
                CONF_TRANSPORT: TRANSPORT_CLOUD,
                CONF_HOST: user_input.get(CONF_MQTT_BROKER, DEFAULT_MQTT_BROKER),
                CONF_DEVICE_ID: device_id,
                CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY].strip(),
                CONF_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
                CONF_MQTT_BROKER: user_input.get(
                    CONF_MQTT_BROKER, DEFAULT_MQTT_BROKER
                ),
                CONF_MQTT_PORT: user_input.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT),
                CONF_MQTT_CLIENT_ID: user_input[CONF_MQTT_CLIENT_ID].strip(),
                CONF_MQTT_USERNAME: user_input[CONF_MQTT_USERNAME].strip(),
                CONF_MQTT_PASSWORD: user_input[CONF_MQTT_PASSWORD].strip(),
                CONF_MQTT_SOURCE_ID: int(user_input[CONF_MQTT_SOURCE_ID]),
            }

            if await self._async_cloud_reachable(data, errors):
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=data
                )

        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(
                CLOUD_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def _async_cloud_reachable(
        self, data: dict[str, Any], errors: dict[str, str]
    ) -> bool:
        """Open the broker session once, so bad credentials fail here."""
        device = ZksjCloudDevice(
            self.hass,
            device_id=data[CONF_DEVICE_ID],
            local_key=data[CONF_LOCAL_KEY],
            broker=data[CONF_MQTT_BROKER],
            port=data[CONF_MQTT_PORT],
            client_id=data[CONF_MQTT_CLIENT_ID],
            username=data[CONF_MQTT_USERNAME],
            password=data[CONF_MQTT_PASSWORD],
            source_id=data[CONF_MQTT_SOURCE_ID],
        )
        try:
            await device.async_connect()
        except ZksjConnectionError as err:
            _LOGGER.debug("Cloud connect failed: %s", err)
            errors["base"] = "cannot_connect"
            return False
        except Exception:  # noqa: BLE001 - paho raises broadly
            _LOGGER.exception("Unexpected error opening the broker session")
            errors["base"] = "unknown"
            return False
        finally:
            await device.async_close()
        return True

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find the pump on the LAN, for one that answers local control."""
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
            step_id="local",
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
                local_key=user_input.get(CONF_LOCAL_KEY, ""),
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
                local_key=user_input.get(CONF_LOCAL_KEY, ""),
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

        if not local_key:
            # Monitor-only: without the key there is nothing to probe, and
            # nothing to get wrong. The entry comes up with reachability
            # alone and can be given a key later via Reconfigure.
            return self.async_create_entry(
                title=name,
                data={
                    CONF_TRANSPORT: TRANSPORT_LOCAL,
                    CONF_HOST: host,
                    CONF_DEVICE_ID: device_id,
                    CONF_LOCAL_KEY: "",
                    CONF_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
                },
            )

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
                CONF_TRANSPORT: TRANSPORT_LOCAL,
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
        if entry.data.get(CONF_TRANSPORT) == TRANSPORT_CLOUD:
            return await self.async_step_reconfigure_cloud(user_input)
        return await self.async_step_reconfigure_local(user_input)

    async def async_step_reconfigure_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the broker credentials once the session behind them ends.

        This is the routine maintenance of a cloud entry rather than an
        exception: the credentials come from the app's own login and cannot
        be renewed without it, so when the pump goes unavailable this is the
        step that brings it back -- re-run the sniff script, paste, submit.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                **entry.data,
                CONF_MQTT_USERNAME: user_input[CONF_MQTT_USERNAME].strip(),
                CONF_MQTT_PASSWORD: user_input[CONF_MQTT_PASSWORD].strip(),
                CONF_MQTT_CLIENT_ID: user_input[CONF_MQTT_CLIENT_ID].strip(),
                CONF_MQTT_SOURCE_ID: int(user_input[CONF_MQTT_SOURCE_ID]),
                CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY].strip(),
            }
            if await self._async_cloud_reachable(data, errors):
                return self.async_update_reload_and_abort(entry, data_updates=data)

        current = user_input or entry.data
        return self.async_show_form(
            step_id="reconfigure_cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MQTT_USERNAME,
                        default=current.get(CONF_MQTT_USERNAME, ""),
                    ): cv.string,
                    vol.Required(
                        CONF_MQTT_PASSWORD,
                        default=current.get(CONF_MQTT_PASSWORD, ""),
                    ): cv.string,
                    vol.Required(
                        CONF_MQTT_CLIENT_ID,
                        default=current.get(CONF_MQTT_CLIENT_ID, ""),
                    ): cv.string,
                    vol.Required(
                        CONF_MQTT_SOURCE_ID,
                        default=current.get(CONF_MQTT_SOURCE_ID, 0),
                    ): cv.positive_int,
                    vol.Required(
                        CONF_LOCAL_KEY, default=current.get(CONF_LOCAL_KEY, "")
                    ): cv.string,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-point a local entry at a new address or key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            local_key = user_input.get(CONF_LOCAL_KEY, "").strip()
            if not local_key:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_LOCAL_KEY: ""},
                )
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
            step_id="reconfigure_local",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): cv.string,
                    vol.Optional(
                        CONF_LOCAL_KEY, default=entry.data.get(CONF_LOCAL_KEY, "")
                    ): cv.string,
                }
            ),
            errors=errors,
        )
