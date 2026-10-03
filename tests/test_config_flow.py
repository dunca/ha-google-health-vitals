"""Tests for the Google Health Vitals config flow."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.google_health_vitals.const import CONF_SOURCE_ENTRY_ID, DOMAIN


async def test_creates_entry(hass: HomeAssistant, source_entry: MockConfigEntry) -> None:
    """Picking the Google Health account creates an entry tied to it."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_ENTRY_ID: source_entry.entry_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alex"
    assert result["data"] == {CONF_SOURCE_ENTRY_ID: source_entry.entry_id}
    assert result["result"].unique_id == "1234567890"


async def test_needs_google_health(hass: HomeAssistant) -> None:
    """Without Google Health there's nothing to borrow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_google_health"


async def test_already_configured(
    hass: HomeAssistant, source_entry: MockConfigEntry, vitals_entry: MockConfigEntry
) -> None:
    """An account can only be added once."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
