"""Tests for picking data points out of API responses."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from google_health_api.model import (
    DailyOxygenSaturation,
    DailySleepTemperatureDerivations,
    Sleep,
)

# Load selection.py directly so the tests don't need Home Assistant installed.
_PATH = Path(__file__).parents[1] / "custom_components/google_health_vitals/selection.py"
_spec = importlib.util.spec_from_file_location("selection", _PATH)
selection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(selection)


def _spo2(day: int, avg: float) -> DailyOxygenSaturation:
    return DailyOxygenSaturation.from_dict(
        {
            "averagePercentage": avg,
            "lowerBoundPercentage": avg - 2,
            "upperBoundPercentage": avg + 2,
            "date": {"year": 2026, "month": 10, "day": day},
        }
    )


def _sleep(start: str, end: str | None, stages: list[dict] | None, nap: bool = False) -> Sleep:
    interval = {"startTime": start}
    if end:
        interval["endTime"] = end
    return Sleep.from_dict(
        {
            "interval": interval,
            "type": "STAGES",
            "metadata": {"nap": nap},
            "summary": {"minutesAsleep": 451, "stagesSummary": stages or []},
        }
    )


def test_latest_daily_picks_newest_date_regardless_of_order() -> None:
    points = [_spo2(1, 95.0), _spo2(3, 96.5), _spo2(2, 94.0)]
    assert selection.latest_daily(points).average_percentage == 96.5


def test_latest_daily_empty() -> None:
    assert selection.latest_daily([]) is None


def test_latest_daily_crosses_month() -> None:
    sept = DailySleepTemperatureDerivations.from_dict(
        {"date": {"year": 2026, "month": 9, "day": 30}, "nightlyTemperatureCelsius": 34.0}
    )
    octo = DailySleepTemperatureDerivations.from_dict(
        {"date": {"year": 2026, "month": 10, "day": 1}, "nightlyTemperatureCelsius": 33.5}
    )
    assert selection.latest_daily([octo, sept]) is octo


def test_main_sleep_prefers_night_over_later_nap() -> None:
    night = _sleep("2026-10-02T20:24:00Z", "2026-10-03T04:01:00Z", [])
    nap = _sleep("2026-10-03T12:00:00Z", "2026-10-03T12:30:00Z", [], nap=True)
    assert selection.main_sleep([nap, night]) is night


def test_main_sleep_skips_in_progress_and_picks_latest() -> None:
    older = _sleep("2026-10-01T21:00:00Z", "2026-10-02T05:00:00Z", [])
    newer = _sleep("2026-10-02T20:24:00Z", "2026-10-03T04:01:00+00:00", [])
    running = _sleep("2026-10-03T20:00:00Z", None, [])
    assert selection.main_sleep([older, running, newer]) is newer


def test_main_sleep_falls_back_to_nap() -> None:
    nap = _sleep("2026-10-03T12:00:00Z", "2026-10-03T12:30:00Z", [], nap=True)
    assert selection.main_sleep([nap]) is nap


def test_stage_minutes() -> None:
    session = _sleep(
        "2026-10-02T20:24:00Z",
        "2026-10-03T04:01:00Z",
        [
            {"type": "DEEP", "minutes": 62, "count": 4},
            {"type": "LIGHT", "minutes": 280, "count": 30},
            {"type": "AWAKE", "minutes": 6, "count": 12},
        ],
    )
    assert selection.stage_minutes(session, "DEEP") == 62
    assert selection.stage_minutes(session, "LIGHT") == 280
    # Staged session without REM listed means zero REM, not unknown.
    assert selection.stage_minutes(session, "REM") == 0


def test_stage_minutes_classic_session_is_unknown() -> None:
    session = _sleep(
        "2026-10-02T20:24:00Z",
        "2026-10-03T04:01:00Z",
        [{"type": "ASLEEP", "minutes": 451}, {"type": "RESTLESS", "minutes": 4}],
    )
    assert selection.stage_minutes(session, "DEEP") is None
    assert selection.stage_minutes(None, "DEEP") is None


def test_parse_time() -> None:
    assert selection.parse_time("2026-10-03T04:01:00Z").hour == 4
    assert selection.parse_time("not a time") is None
    assert selection.parse_time(None) is None


def test_main_sleep_ignores_sessions_before_since() -> None:
    old = _sleep("2026-09-28T21:00:00Z", "2026-09-29T05:00:00Z", [])
    assert selection.main_sleep([old], since=datetime(2026, 10, 1, tzinfo=UTC)) is None
    assert selection.main_sleep([old]) is old
