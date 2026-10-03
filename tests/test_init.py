"""Tests for setting up Google Health Vitals."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from google_health_api.exceptions import (
    HealthApiConnectionException,
    HealthApiScopeInsufficientException,
    HealthAuthException,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.google_health_vitals.const import (
    DOMAIN,
    GOOGLE_HEALTH_DOMAIN,
    RETRY_INTERVAL,
    UPDATE_INTERVAL,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors(
    hass: HomeAssistant,
    vitals_entry: MockConfigEntry,
    source_entry: MockConfigEntry,
    mock_api: MagicMock,
) -> None:
    """Every metric becomes a sensor on the Google Health account device."""
    await _setup(hass, vitals_entry)
    assert vitals_entry.state is ConfigEntryState.LOADED

    spo2 = hass.states.get("sensor.alex_vitals_blood_oxygen")
    assert spo2.state == "96.4"
    assert spo2.attributes["date"] == "2026-10-03"
    assert spo2.attributes["lower_bound"] == 93.0
    assert hass.states.get("sensor.alex_vitals_heart_rate_variability").state == "48.2"
    assert hass.states.get("sensor.alex_vitals_breathing_rate").state == "14.6"
    assert hass.states.get("sensor.alex_vitals_skin_temperature_variation").state == "-0.3"
    vo2 = hass.states.get("sensor.alex_vitals_cardio_fitness")
    assert vo2.state == "44.5"
    assert vo2.attributes["fitness_level"] == "GOOD"
    deep = hass.states.get("sensor.alex_vitals_deep_sleep")
    assert deep.state == "1.033417"
    assert deep.attributes["minutes"] == 62
    assert deep.attributes["unit_of_measurement"] == "h"
    assert hass.states.get("sensor.alex_vitals_time_asleep").attributes["minutes"] == 451
    assert hass.states.get("sensor.alex_vitals_time_asleep").state == "7.51675"
    assert hass.states.get("sensor.alex_vitals_light_sleep").attributes["minutes"] == 280
    assert hass.states.get("sensor.alex_vitals_rem_sleep").attributes["minutes"] == 109
    assert hass.states.get("sensor.alex_vitals_bedtime").state == "2026-10-02T20:24:00+00:00"
    assert hass.states.get("sensor.alex_vitals_wake_time").state == "2026-10-03T04:01:00+00:00"

    registry = dr.async_get(hass)
    account = registry.async_get_device_by_identifier(
        (GOOGLE_HEALTH_DOMAIN, source_entry.entry_id), source_entry.entry_id
    )
    device = registry.async_get_device_by_identifier(
        (DOMAIN, vitals_entry.entry_id), vitals_entry.entry_id
    )
    assert device.name == "Alex vitals"
    assert device.via_device_id == account.id
    entity = er.async_get(hass).async_get("sensor.alex_vitals_blood_oxygen")
    assert entity.device_id == device.id
    assert entity.unique_id == "1234567890_oxygen_saturation"

    assert await hass.config_entries.async_unload(vitals_entry.entry_id)


async def test_metric_not_granted_is_unknown(
    hass: HomeAssistant, vitals_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    """A metric the account can't read leaves only that sensor unknown."""
    mock_api.daily_vo2_max.list = AsyncMock(side_effect=HealthApiScopeInsufficientException("nope"))
    await _setup(hass, vitals_entry)
    assert hass.states.get("sensor.alex_vitals_cardio_fitness").state == STATE_UNKNOWN
    assert hass.states.get("sensor.alex_vitals_blood_oxygen").state == "96.4"


async def test_transient_error_keeps_last_value(
    hass: HomeAssistant, vitals_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    """One failed request keeps that sensor's value and retries soon."""
    await _setup(hass, vitals_entry)
    coordinator = vitals_entry.runtime_data
    working = mock_api.daily_oxygen_saturation.list
    mock_api.daily_oxygen_saturation.list = AsyncMock(
        side_effect=HealthApiConnectionException("503")
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.alex_vitals_blood_oxygen").state == "96.4"
    assert coordinator.update_interval == RETRY_INTERVAL

    # Back to the normal pace once it succeeds.
    mock_api.daily_oxygen_saturation.list = working
    await coordinator.async_refresh()
    assert coordinator.update_interval == UPDATE_INTERVAL


async def test_auth_error_makes_sensors_unavailable(
    hass: HomeAssistant, vitals_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    """A rejected token marks sensors unavailable without starting a reauth here."""
    await _setup(hass, vitals_entry)
    mock_api.sleep.list = AsyncMock(side_effect=HealthAuthException("expired"))
    await vitals_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.alex_vitals_blood_oxygen").state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress()


async def test_source_not_loaded_retries(
    hass: HomeAssistant,
    vitals_entry: MockConfigEntry,
    source_entry: MockConfigEntry,
    mock_api: MagicMock,
) -> None:
    """Setup waits while Google Health is not loaded."""
    source_entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    await _setup(hass, vitals_entry)
    assert vitals_entry.state is ConfigEntryState.SETUP_RETRY


async def test_source_removed(
    hass: HomeAssistant,
    vitals_entry: MockConfigEntry,
    source_entry: MockConfigEntry,
    mock_api: MagicMock,
) -> None:
    """Setup fails for good when the Google Health entry is gone."""
    await hass.config_entries.async_remove(source_entry.entry_id)
    await _setup(hass, vitals_entry)
    assert vitals_entry.state is ConfigEntryState.SETUP_ERROR


async def test_sleep_requested_without_time_filter(
    hass: HomeAssistant, vitals_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    """The API rejects a time filter on sleep, so none is sent."""
    await _setup(hass, vitals_entry)
    assert "start_time" not in mock_api.sleep.list.call_args.kwargs


async def test_persistent_failure_is_logged_once_as_warning(
    hass: HomeAssistant,
    vitals_entry: MockConfigEntry,
    mock_api: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing metric warns once, then stays quiet until it recovers."""
    mock_api.sleep.list = AsyncMock(side_effect=HealthApiConnectionException("bad request"))
    await _setup(hass, vitals_entry)
    await vitals_entry.runtime_data.async_refresh()
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "sleep" in r.message]
    assert len(warnings) == 1
