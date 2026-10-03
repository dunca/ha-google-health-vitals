"""Data coordinator for Google Health Vitals."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from google_health_api import GoogleHealthApi
from google_health_api.exceptions import (
    GoogleHealthApiError,
    HealthApiForbiddenException,
    HealthApiNotFoundException,
    HealthAuthException,
)
from google_health_api.model import (
    DailyHeartRateVariability,
    DailyOxygenSaturation,
    DailyRespiratoryRate,
    DailySleepTemperatureDerivations,
    DailyVO2Max,
    Sleep,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DAILY_LOOKBACK, DOMAIN, SLEEP_LOOKBACK, UPDATE_INTERVAL
from .selection import latest_daily, main_sleep

if TYPE_CHECKING:
    from . import VitalsConfigEntry

_LOGGER = logging.getLogger(__name__)

DAILY_PAGE_SIZE = 10
SLEEP_PAGE_SIZE = 25


@dataclass(frozen=True)
class VitalsData:
    """Newest value of each metric, or None when there is none."""

    oxygen_saturation: DailyOxygenSaturation | None = None
    heart_rate_variability: DailyHeartRateVariability | None = None
    respiratory_rate: DailyRespiratoryRate | None = None
    skin_temperature: DailySleepTemperatureDerivations | None = None
    vo2_max: DailyVO2Max | None = None
    sleep: Sleep | None = None


class VitalsCoordinator(DataUpdateCoordinator[VitalsData]):
    """Fetch every metric in one pass every 30 minutes."""

    config_entry: VitalsConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VitalsConfigEntry,
        source: ConfigEntry,
        api: GoogleHealthApi,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.api = api
        self.source = source
        self._unavailable: set[str] = set()
        self._failing: set[str] = set()

    async def _async_update_data(self) -> VitalsData:
        now = dt_util.utcnow()
        daily_since = now - DAILY_LOOKBACK
        requests: dict[str, Awaitable[Any]] = {
            "oxygen_saturation": self.api.daily_oxygen_saturation.list(
                start_time=daily_since, page_size=DAILY_PAGE_SIZE
            ),
            "heart_rate_variability": self.api.daily_heart_rate_variability.list(
                start_time=daily_since, page_size=DAILY_PAGE_SIZE
            ),
            "respiratory_rate": self.api.daily_respiratory_rate.list(
                start_time=daily_since, page_size=DAILY_PAGE_SIZE
            ),
            "skin_temperature": self.api.daily_sleep_temperature_derivations.list(
                start_time=daily_since, page_size=DAILY_PAGE_SIZE
            ),
            "vo2_max": self.api.daily_vo2_max.list(
                start_time=daily_since, page_size=DAILY_PAGE_SIZE
            ),
            # The API rejects a time filter on sleep sessions (400
            # INVALID_DATA_POINT_FILTER_DATA_TYPE_MEMBER), so fetch the newest
            # sessions unfiltered and drop old ones in main_sleep.
            "sleep": self.api.sleep.list(page_size=SLEEP_PAGE_SIZE),
        }
        results = await asyncio.gather(*requests.values(), return_exceptions=True)

        previous = self.data or VitalsData()
        values: dict[str, Any] = {}
        transient_failures = 0
        for key, result in zip(requests, results, strict=True):
            if isinstance(result, HealthAuthException):
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="source_auth",
                    translation_placeholders={"title": self.source.title},
                ) from result
            if isinstance(result, (HealthApiForbiddenException, HealthApiNotFoundException)):
                # Not granted or not offered for this account: report it once.
                if key not in self._unavailable:
                    _LOGGER.info("%s is not available: %s", key, result)
                    self._unavailable.add(key)
                values[key] = None
                continue
            if isinstance(result, GoogleHealthApiError):
                # Warn on the first failure so a persistent one isn't hidden.
                log = _LOGGER.debug if key in self._failing else _LOGGER.warning
                log("Fetching %s failed, keeping last value: %s", key, result)
                self._failing.add(key)
                transient_failures += 1
                values[key] = getattr(previous, key)
                continue
            if isinstance(result, BaseException):
                raise result

            self._unavailable.discard(key)
            self._failing.discard(key)
            payloads = [point.data for point in result.data_points]
            values[key] = (
                main_sleep(payloads, since=now - SLEEP_LOOKBACK)
                if key == "sleep"
                else latest_daily(payloads)
            )

        if transient_failures == len(requests):
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="communication_error")
        return replace(VitalsData(), **values)
