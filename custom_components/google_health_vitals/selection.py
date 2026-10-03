"""Pick the relevant data point out of a Google Health list response.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from google_health_api.model import Sleep


class _HasDate(Protocol):
    date: Any


def _date_key(payload: _HasDate) -> tuple[int, int, int]:
    date = payload.date
    if date is None:
        return (0, 0, 0)
    return (date.year or 0, date.month or 0, date.day or 0)


def latest_daily[T: _HasDate](payloads: Iterable[T]) -> T | None:
    """Return the payload with the newest calendar date."""
    return max(payloads, key=_date_key, default=None)


def parse_time(value: str | None) -> datetime | None:
    """Parse an RFC 3339 timestamp from the API."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main_sleep(sessions: Iterable[Sleep]) -> Sleep | None:
    """Return the most recent finished, non-nap sleep session.

    Falls back to the most recent finished nap when no main sleep exists, so a
    day with only a nap still shows something.
    """
    finished = [
        session
        for session in sessions
        if session.summary is not None and parse_time(session.end_time) is not None
    ]
    if not finished:
        return None

    def end(session: Sleep) -> datetime:
        return parse_time(session.end_time)  # type: ignore[return-value]

    main = [s for s in finished if not (s.metadata and s.metadata.nap)]
    return max(main or finished, key=end)


def stage_minutes(session: Sleep | None, stage: str) -> int | None:
    """Return the total minutes spent in one sleep stage, e.g. ``DEEP``."""
    if session is None or session.summary is None:
        return None
    stages = session.summary.stages_summary
    if not stages:
        return None
    for summary in stages:
        if (summary.type or "").upper() == stage:
            return summary.minutes or 0
    # Staged sessions omit a stage with zero minutes; classic ones have none.
    if any((s.type or "").upper() in ("DEEP", "LIGHT", "REM") for s in stages):
        return 0
    return None
