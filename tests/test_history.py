"""Tests for importing history into long-term statistics."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from google_health_api.model import (
    DailyOxygenSaturation,
    DailyRestingHeartRate,
    Sleep,
    StepsRollupValue,
)
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.google_health_vitals.const import DOMAIN, GOOGLE_HEALTH_DOMAIN
from custom_components.google_health_vitals.history import (
    cumulative_rows,
    recorded_sum_adjustment,
    row_start,
    sleep_by_wake_day,
)

TZ = ZoneInfo("Europe/Bucharest")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations: None) -> None:
    """Start the recorder before Home Assistant, as the recorder fixture requires."""


def test_row_start_is_local_midnight_on_a_utc_hour() -> None:
    assert row_start(date(2026, 10, 1), TZ) == datetime(2026, 9, 30, 21, tzinfo=UTC)
    # Half-hour zone: rounded down to the hour.
    assert row_start(date(2026, 10, 1), ZoneInfo("Asia/Kolkata")).minute == 0


def test_cumulative_rows_count_up_from_zero() -> None:
    values = {date(2026, 9, 29): 8000.0, date(2026, 9, 30): 6000.0, date(2026, 10, 3): 999.0}
    first_recorded = datetime(2026, 10, 3, 13, tzinfo=UTC)
    rows = cumulative_rows(values, TZ, first_recorded)
    # The day that's already recorded is left out.
    assert [row["start"].astimezone(TZ).date() for row in rows] == [
        date(2026, 9, 29),
        date(2026, 9, 30),
    ]
    assert [row["sum"] for row in rows] == [8000.0, 14000.0]
    assert rows[0]["state"] == 8000.0


def test_recorded_sum_adjustment_keeps_the_first_recorded_change() -> None:
    rows = cumulative_rows({date(2026, 9, 30): 6000.0}, TZ, datetime(2026, 10, 3, tzinfo=UTC))
    # Recorded: 3000 steps so far today, sum restarted at 0.
    assert recorded_sum_adjustment(rows, {"state": 3000.0, "sum": 0.0}) == 9000.0
    # Already continuous (a re-run): nothing to shift.
    assert recorded_sum_adjustment(rows, {"state": 3000.0, "sum": 9000.0}) == 0.0


def _sleep(start: str, end: str, minutes: int, nap: bool = False) -> Sleep:
    return Sleep.from_dict(
        {
            "interval": {"startTime": start, "endTime": end},
            "metadata": {"nap": nap},
            "summary": {"minutesAsleep": minutes, "minutesInSleepPeriod": minutes + 6},
        }
    )


def test_sleep_by_wake_day_keys_on_local_wake_day_and_skips_naps() -> None:
    night = _sleep("2026-10-01T20:30:00Z", "2026-10-02T04:00:00Z", 420)
    nap = _sleep("2026-10-02T11:00:00Z", "2026-10-02T11:40:00Z", 35, nap=True)
    late = _sleep("2026-10-02T22:30:00Z", "2026-10-03T05:00:00Z", 370)
    by_day = sleep_by_wake_day([night, nap, late], TZ)
    assert by_day[date(2026, 10, 2)] is night
    assert by_day[date(2026, 10, 3)] is late


def _page(*payloads: object) -> MagicMock:
    page = MagicMock()
    page.data_points = [MagicMock(data=p) for p in payloads]
    return page


class _Pages:
    """Async-iterable list result, like the library's ListDataPointResult."""

    def __init__(self, *pages: MagicMock) -> None:
        self._pages = pages
        self.data_points = pages[0].data_points if pages else []

    async def __aiter__(self):  # noqa: D105
        for page in self._pages:
            yield page


def _rollup(day: date, steps: int) -> MagicMock:
    point = MagicMock()
    point.data = StepsRollupValue.from_dict({"countSum": steps})
    point.civil_start_time.date = MagicMock(year=day.year, month=day.month, day=day.day)
    return point


@pytest.fixture
async def history_api(mock_api: MagicMock) -> MagicMock:
    """Extend the API mock with three days of history."""
    empty = AsyncMock(return_value=_Pages())
    for name in (
        "daily_heart_rate_variability",
        "daily_respiratory_rate",
        "daily_sleep_temperature_derivations",
        "daily_vo2_max",
        "weight",
        "body_fat",
    ):
        getattr(mock_api, name).list = empty
    mock_api.daily_oxygen_saturation.list = AsyncMock(
        return_value=_Pages(
            _page(
                *(
                    DailyOxygenSaturation.from_dict(
                        {
                            "averagePercentage": 95.0 + day,
                            "lowerBoundPercentage": 90.0,
                            "upperBoundPercentage": 99.0,
                            "date": {"year": 2026, "month": 10, "day": day},
                        }
                    )
                    for day in (1, 2, 3)
                )
            )
        )
    )
    mock_api.daily_resting_heart_rate.list = AsyncMock(
        return_value=_Pages(
            _page(
                DailyRestingHeartRate.from_dict(
                    {"beatsPerMinute": 57, "date": {"year": 2026, "month": 10, "day": 2}}
                )
            )
        )
    )
    mock_api.sleep.list = AsyncMock(
        return_value=_Pages(_page(_sleep("2026-10-01T20:30:00Z", "2026-10-02T04:00:00Z", 420)))
    )
    mock_api.steps.daily_rollup = AsyncMock(
        return_value=[_rollup(date(2026, 10, 1), 8000), _rollup(date(2026, 10, 2), 6000)]
    )
    for name in ("distance", "active_energy_burned", "floors"):
        getattr(mock_api, name).daily_rollup = AsyncMock(return_value=[])
    return mock_api


def _register(hass: HomeAssistant, platform: str, unique_id: str, object_id: str) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create("sensor", platform, unique_id, suggested_object_id=object_id)
        .entity_id
    )


async def test_import_history(
    hass: HomeAssistant,
    vitals_entry: MockConfigEntry,
    source_entry: MockConfigEntry,
    history_api: MagicMock,
) -> None:
    """Imported days land before the first recorded hour and chain sums."""
    await hass.config.async_set_time_zone("Europe/Bucharest")
    await hass.config_entries.async_setup(vitals_entry.entry_id)
    await hass.async_block_till_done()

    spo2 = "sensor.alex_vitals_blood_oxygen"
    rhr = _register(
        hass, GOOGLE_HEALTH_DOMAIN, f"{source_entry.entry_id}_resting_heart_rate", "alex_rhr"
    )
    steps = _register(hass, GOOGLE_HEALTH_DOMAIN, f"{source_entry.entry_id}_steps", "alex_steps")

    # What Home Assistant recorded itself, starting 3 Oct 13:00 UTC.
    recorded = datetime(2026, 10, 3, 13, tzinfo=UTC)
    for statistic_id, unit, has_sum, row in (
        (spo2, "%", False, {"start": recorded, "mean": 97.1, "min": 97.1, "max": 97.1}),
        (rhr, "bpm", False, {"start": recorded, "mean": 56, "min": 56, "max": 56}),
        (steps, "steps", True, {"start": recorded, "state": 3000, "sum": 0}),
    ):
        async_import_statistics(
            hass,
            {
                "mean_type": StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC,
                "has_sum": has_sum,
                "name": None,
                "source": "recorder",
                "statistic_id": statistic_id,
                "unit_class": None,
                "unit_of_measurement": unit,
            },
            [row],
        )
    await async_wait_recording_done(hass)

    response = await hass.services.async_call(
        DOMAIN, "import_history", {"days": 10}, blocking=True, return_response=True
    )
    await async_wait_recording_done(hass)

    result = response["Alex"]
    assert result["imported"][spo2] == 3  # 1, 2 and 3 Oct (3 Oct midnight is before 13:00)
    assert result["imported"][rhr] == 1
    assert result["imported"][steps] == 2

    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 9, 1, tzinfo=UTC),
        None,
        {spo2, steps},
        "hour",
        None,
        {"mean", "sum", "state"},
    )
    assert [row["mean"] for row in stats[spo2]] == [96.0, 97.0, 98.0, 97.1]
    step_rows = stats[steps]
    # Change per row: 8000 on 1 Oct, 6000 on 2 Oct, then 3000 recorded on 3 Oct.
    sums = [row["sum"] for row in step_rows]
    assert [b - a for a, b in zip(sums, sums[1:], strict=False)] == [6000, 3000]
    assert step_rows[0]["state"] == 8000
    assert sums[0] == 8000  # counts up from zero, no negative first change

    # Running it again changes nothing.
    await hass.services.async_call(
        DOMAIN, "import_history", {"days": 10}, blocking=True, return_response=True
    )
    await async_wait_recording_done(hass)
    again = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 9, 1, tzinfo=UTC),
        None,
        {spo2, steps},
        "hour",
        None,
        {"mean", "sum", "state"},
    )
    assert [row["sum"] for row in again[steps]] == sums
    assert [row["mean"] for row in again[spo2]] == [96.0, 97.0, 98.0, 97.1]


async def test_import_refuses_to_overlap(
    hass: HomeAssistant, vitals_entry: MockConfigEntry, history_api: MagicMock
) -> None:
    """A second import while one runs is refused instead of double-shifting sums."""
    import asyncio

    from homeassistant.exceptions import ServiceValidationError

    await hass.config_entries.async_setup(vitals_entry.entry_id)
    await hass.async_block_till_done()
    release = asyncio.Event()

    async def slow_rollup(**_: object) -> list:
        await release.wait()
        return []

    history_api.steps.daily_rollup = AsyncMock(side_effect=slow_rollup)
    first = hass.async_create_task(
        hass.services.async_call(DOMAIN, "import_history", {"days": 10}, blocking=True)
    )
    await asyncio.sleep(0)
    for _ in range(20):
        await asyncio.sleep(0)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "import_history", {"days": 10}, blocking=True)
    release.set()
    await first
