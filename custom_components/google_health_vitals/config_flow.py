"""Config flow for Google Health Vitals."""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_SOURCE_ENTRY_ID, DOMAIN, GOOGLE_HEALTH_DOMAIN


class VitalsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick the Google Health account to read vitals for."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        accounts = [
            entry
            for entry in self.hass.config_entries.async_entries(GOOGLE_HEALTH_DOMAIN)
            if entry.disabled_by is None
        ]
        if not accounts:
            return self.async_abort(reason="no_google_health")

        configured = {entry.data[CONF_SOURCE_ENTRY_ID] for entry in self._async_current_entries()}
        available = {
            entry.entry_id: entry.title for entry in accounts if entry.entry_id not in configured
        }
        if not available:
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            source = self.hass.config_entries.async_get_entry(user_input[CONF_SOURCE_ENTRY_ID])
            if source is None:
                return self.async_abort(reason="no_google_health")
            await self.async_set_unique_id(source.unique_id or source.entry_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=source.title, data={CONF_SOURCE_ENTRY_ID: source.entry_id}
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCE_ENTRY_ID, default=next(iter(available))): vol.In(
                        available
                    )
                }
            ),
        )
