"""Actions for Google Health Vitals."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, GOOGLE_HEALTH_DOMAIN
from .history import async_import_history

ATTR_DAYS = "days"
SERVICE_IMPORT_HISTORY = "import_history"

IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DAYS, default=365): vol.All(vol.Coerce(int), vol.Range(min=1, max=3650)),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the import_history action."""

    async def import_history(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")

        response: dict[str, dict[str, dict[str, int | str]]] = {}
        for entry in entries:
            coordinator = entry.runtime_data
            source = coordinator.source
            try:
                result = await async_import_history(
                    hass,
                    coordinator.api,
                    {
                        DOMAIN: source.unique_id or source.entry_id,
                        GOOGLE_HEALTH_DOMAIN: source.entry_id,
                    },
                    call.data[ATTR_DAYS],
                )
            except RuntimeError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="import_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
            response[entry.title] = {"imported": result.imported, "skipped": result.skipped}
        return response

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        import_history,
        schema=IMPORT_HISTORY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
