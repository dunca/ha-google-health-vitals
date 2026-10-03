"""The Google Health Vitals integration."""

from google_health_api import GoogleHealthApi
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.config_entry_oauth2_flow import (
    ImplementationUnavailableError,
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .api import BorrowedEntryAuth
from .const import CONF_SOURCE_ENTRY_ID, DOMAIN
from .coordinator import VitalsCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type VitalsConfigEntry = ConfigEntry[VitalsCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VitalsConfigEntry) -> bool:
    """Set up Google Health Vitals from a config entry."""
    source = hass.config_entries.async_get_entry(entry.data[CONF_SOURCE_ENTRY_ID])
    if source is None:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="source_removed")
    if source.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="source_not_loaded",
            translation_placeholders={"title": source.title},
        )

    try:
        implementation = await async_get_config_entry_implementation(hass, source)
    except ImplementationUnavailableError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="oauth_unavailable"
        ) from err

    auth = BorrowedEntryAuth(
        aiohttp_client.async_get_clientsession(hass),
        OAuth2Session(hass, source, implementation),
    )
    coordinator = VitalsCoordinator(hass, entry, source, GoogleHealthApi(auth))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VitalsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
