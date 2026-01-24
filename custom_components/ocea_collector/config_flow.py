"""Config flow for Ocea Collector."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import selector

from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL_CHOICES,
)


class OceaCollectorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ocea Collector."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_USERNAME],
                data={
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): selector({"text": {"type": "password"}}),
                vol.Required(
                    CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
                ): vol.In(UPDATE_INTERVAL_CHOICES),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OceaCollectorOptionsFlow(config_entry)


class OceaCollectorOptionsFlow(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        update_interval = self._config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            self._config_entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_UPDATE_INTERVAL, default=update_interval): vol.In(
                    UPDATE_INTERVAL_CHOICES
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
