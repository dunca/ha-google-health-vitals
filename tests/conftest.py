"""Fixtures for Google Health Vitals tests."""

import time
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google_health_api.model import (
    DailyHeartRateVariability,
    DailyOxygenSaturation,
    DailyRespiratoryRate,
    DailySleepTemperatureDerivations,
    DailyVO2Max,
    Sleep,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.google_health_vitals.const import (
    CONF_SOURCE_ENTRY_ID,
    DOMAIN,
    GOOGLE_HEALTH_DOMAIN,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration."""


@pytest.fixture(autouse=True)
def skip_google_health_dependency() -> Generator[None]:
    """Don't set up the real Google Health integration as a dependency."""
    with patch(
        "homeassistant.loader.Integration.dependencies",
        new_callable=lambda: property(
            lambda self: [] if self.domain == DOMAIN else self.manifest.get("dependencies", [])
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def frozen_now(freezer) -> None:
    """Pin the clock to the morning after the fixture night."""
    freezer.move_to("2026-10-03T12:00:00+00:00")


@pytest.fixture
def source_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A loaded Google Health entry with its account device."""
    entry = MockConfigEntry(
        domain=GOOGLE_HEALTH_DOMAIN,
        title="Alex",
        unique_id="1234567890",
        data={
            "auth_implementation": "google_health_test",
            "token": {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_at": time.time() + 3600,
                "scope": "",
            },
        },
        state=ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(GOOGLE_HEALTH_DOMAIN, entry.entry_id)},
        manufacturer="Google",
        name="Alex",
    )
    return entry


@pytest.fixture
def vitals_entry(hass: HomeAssistant, source_entry: MockConfigEntry) -> MockConfigEntry:
    """The integration's own entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Alex",
        unique_id=source_entry.unique_id,
        data={CONF_SOURCE_ENTRY_ID: source_entry.entry_id},
    )
    entry.add_to_hass(hass)
    return entry


def _page(*payloads: object) -> MagicMock:
    page = MagicMock()
    page.data_points = [MagicMock(data=payload) for payload in payloads]
    return page


def _date(day: int) -> dict[str, int]:
    return {"year": 2026, "month": 10, "day": day}


@pytest.fixture
def mock_api() -> Generator[MagicMock]:
    """Patch the API client with one night of data."""
    api = MagicMock()
    api.daily_oxygen_saturation.list = AsyncMock(
        return_value=_page(
            DailyOxygenSaturation.from_dict(
                {
                    "averagePercentage": 95.1,
                    "lowerBoundPercentage": 92.0,
                    "upperBoundPercentage": 98.0,
                    "date": _date(2),
                }
            ),
            DailyOxygenSaturation.from_dict(
                {
                    "averagePercentage": 96.4,
                    "lowerBoundPercentage": 93.0,
                    "upperBoundPercentage": 99.0,
                    "date": _date(3),
                }
            ),
        )
    )
    api.daily_heart_rate_variability.list = AsyncMock(
        return_value=_page(
            DailyHeartRateVariability.from_dict(
                {
                    "date": _date(3),
                    "averageHeartRateVariabilityMilliseconds": 48.2,
                    "nonRemHeartRateBeatsPerMinute": 54,
                }
            )
        )
    )
    api.daily_respiratory_rate.list = AsyncMock(
        return_value=_page(
            DailyRespiratoryRate.from_dict({"date": _date(3), "breathsPerMinute": 14.6})
        )
    )
    api.daily_sleep_temperature_derivations.list = AsyncMock(
        return_value=_page(
            DailySleepTemperatureDerivations.from_dict(
                {
                    "date": _date(3),
                    "nightlyTemperatureCelsius": 34.1,
                    "baselineTemperatureCelsius": 34.4,
                }
            )
        )
    )
    api.daily_vo2_max.list = AsyncMock(
        return_value=_page(
            DailyVO2Max.from_dict({"date": _date(1), "vo2Max": 44.5, "cardioFitnessLevel": "GOOD"})
        )
    )
    api.sleep.list = AsyncMock(
        return_value=_page(
            Sleep.from_dict(
                {
                    "interval": {
                        "startTime": "2026-10-02T20:24:00Z",
                        "endTime": "2026-10-03T04:01:00Z",
                    },
                    "type": "STAGES",
                    "metadata": {"nap": False},
                    "summary": {
                        "minutesAsleep": 451,
                        "minutesInSleepPeriod": 457,
                        "stagesSummary": [
                            {"type": "DEEP", "minutes": 62},
                            {"type": "LIGHT", "minutes": 280},
                            {"type": "REM", "minutes": 109},
                            {"type": "AWAKE", "minutes": 6},
                        ],
                    },
                }
            )
        )
    )
    with (
        patch(
            "custom_components.google_health_vitals.GoogleHealthApi",
            return_value=api,
        ),
        patch(
            "custom_components.google_health_vitals.async_get_config_entry_implementation",
            return_value=MagicMock(),
        ),
    ):
        yield api
