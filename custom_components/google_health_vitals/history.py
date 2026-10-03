"""Import past days from the Google Health API into long-term statistics.

Each metric becomes one hourly statistics row per day, at local midnight of the
day it belongs to (the wake-up day for sleep), so day-period charts pick it up.
Rows are only written for hours before the entity's first recorded statistic,
so nothing Home Assistant recorded itself is touched.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from google_health_api import GoogleHealthApi
from google_health_api.exceptions import (
    GoogleHealthApiError,
    HealthApiForbiddenException,
    HealthApiNotFoundException,
    HealthApiRateLimitException,
    HealthAuthException,
)
from google_health_api.model import Sleep
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData
from homeassistant.components.recorder.statistics import (
    STATISTIC_UNIT_TO_UNIT_CONVERTER,
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfMass,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN, GOOGLE_HEALTH_DOMAIN
from .selection import main_sleep, parse_time, stage_minutes

_LOGGER = logging.getLogger(__name__)

DAILY_PAGE_SIZE = 100
SLEEP_PAGE_SIZE = 25
# The API caps a daily rollup request at 90 days, or 14 for total calories.
ROLLUP_CHUNK_DAYS = 90
SHORT_ROLLUP_CHUNK_DAYS = 14
MAX_PAGES = 400
# Back-off when the API asks us to slow down: 5, 10, 20, 40, 80 seconds.
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_FIRST_WAIT = 5


@dataclass
class Series:
    """Daily values for one metric, keyed by local calendar day."""

    integration: str  # whose entity holds the statistics
    key: str  # that integration's sensor key
    unit: str | None  # unit the values are in
    cumulative: bool = False  # True for totals that reset daily, e.g. steps
    values: dict[date, float] = field(default_factory=dict)


@dataclass
class ImportResult:
    """What an import did, per entity."""

    imported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def _civil_date(value: Any) -> date | None:
    if value is None or value.year is None:
        return None
    return date(value.year, value.month or 1, value.day or 1)


async def _pages(first_page: Any) -> AsyncIterator[Any]:
    count = 0
    async for page in first_page:
        yield page
        count += 1
        if count >= MAX_PAGES:
            return


async def _daily(sub_api: Any, since: date) -> list[Any]:
    """Fetch every payload of a date-keyed daily type since a day."""
    start = datetime.combine(since, time(), tzinfo=dt_util.UTC)
    first = await sub_api.list(start_time=start, page_size=DAILY_PAGE_SIZE)
    return [point.data async for page in _pages(first) for point in page.data_points]


async def _newest_first(
    sub_api: Any, stop_before: datetime, when: Callable[[Any], datetime | None]
) -> list[Any]:
    """Page through a type the API returns newest first, until older than stop_before.

    For types whose time filter the API rejects (sleep) or that core reads
    unfiltered (weight, body fat).
    """
    payloads: list[Any] = []
    first = await sub_api.list(page_size=SLEEP_PAGE_SIZE)
    async for page in _pages(first):
        oldest: datetime | None = None
        for point in page.data_points:
            moment = when(point.data)
            if moment is None:
                continue
            oldest = moment if oldest is None else min(oldest, moment)
            if moment >= stop_before:
                payloads.append(point.data)
        if oldest is not None and oldest < stop_before:
            break
    return payloads


async def _rollups(
    sub_api: Any, since: date, until: date, chunk_days: int = ROLLUP_CHUNK_DAYS
) -> list[Any]:
    """Fetch daily rollups in chunks, oldest first."""
    points: list[Any] = []
    start = since
    while start < until:
        end = min(start + timedelta(days=chunk_days), until)
        points.extend(await sub_api.daily_rollup(start_date=start, end_date=end))
        start = end
    return points


def _local_day(moment: datetime | None, tz: ZoneInfo) -> date | None:
    return moment.astimezone(tz).date() if moment else None


def _sample_time(payload: Any) -> datetime | None:
    return parse_time(payload.sample_time.physical_time)


def _mean_by_day(pairs: Iterable[tuple[date | None, float | None]]) -> dict[date, float]:
    grouped: dict[date, list[float]] = defaultdict(list)
    for day, value in pairs:
        if day is not None and value is not None:
            grouped[day].append(value)
    return {day: sum(vals) / len(vals) for day, vals in grouped.items()}


def sleep_by_wake_day(sessions: Iterable[Sleep], tz: ZoneInfo) -> dict[date, Sleep]:
    """Pick each day's main sleep, keyed by the local day it ended."""
    by_day: dict[date, list[Sleep]] = defaultdict(list)
    for session in sessions:
        day = _local_day(parse_time(session.end_time), tz)
        if day is not None:
            by_day[day].append(session)
    return {day: chosen for day, group in by_day.items() if (chosen := main_sleep(group))}


async def fetch_history(
    api: GoogleHealthApi, days: int, tz: ZoneInfo
) -> tuple[list[Series], dict[str, str]]:
    """Fetch `days` days of every supported metric."""
    today = dt_util.now(tz).date()
    since = today - timedelta(days=days)
    since_dt = datetime.combine(since, time(), tzinfo=tz)
    series: list[Series] = []
    unavailable: dict[str, str] = {}

    async def collect(name: str, fetch: Callable[[], Any]) -> Any:
        """Fetch one metric; a failure skips that metric, not the import.

        A rejected sign-in still aborts, since nothing else would work either.
        """
        wait = RATE_LIMIT_FIRST_WAIT
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                return await fetch()
            except HealthAuthException:
                raise
            except HealthApiRateLimitException as err:
                if attempt == RATE_LIMIT_RETRIES:
                    unavailable[name] = f"rate limited: {err}"
                    return None
                _LOGGER.debug("Rate limited fetching %s, waiting %ss", name, wait)
                await asyncio.sleep(wait)
                wait *= 2
            except (HealthApiForbiddenException, HealthApiNotFoundException) as err:
                unavailable[name] = f"not available: {err}"
                return None
            except GoogleHealthApiError as err:
                unavailable[name] = f"failed: {err}"
                return None
        return None

    def add(
        integration: str, key: str, unit: str | None, values: dict[date, float], **kw: Any
    ) -> None:
        series.append(Series(integration, key, unit, values=values, **kw))

    # Daily, date-keyed metrics (time filter works for these).
    daily_specs: list[tuple[str, Any, str | None, Callable[[Any], float | None]]] = [
        (
            "oxygen_saturation",
            api.daily_oxygen_saturation,
            PERCENTAGE,
            lambda p: p.average_percentage,
        ),
        (
            "heart_rate_variability",
            api.daily_heart_rate_variability,
            UnitOfTime.MILLISECONDS,
            lambda p: p.average_heart_rate_variability_milliseconds,
        ),
        ("respiratory_rate", api.daily_respiratory_rate, "br/min", lambda p: p.breaths_per_minute),
        (
            "skin_temperature_variation",
            api.daily_sleep_temperature_derivations,
            "°C",
            lambda p: (
                p.nightly_temperature_celsius - p.baseline_temperature_celsius
                if p.baseline_temperature_celsius is not None
                else None
            ),
        ),
        ("vo2_max", api.daily_vo2_max, "mL/kg/min", lambda p: p.vo2_max),
    ]
    for key, sub_api, unit, value_fn in daily_specs:
        payloads = await collect(key, lambda s=sub_api: _daily(s, since))
        if payloads:
            add(
                DOMAIN,
                key,
                unit,
                _mean_by_day((_civil_date(p.date), value_fn(p)) for p in payloads),
            )

    rhr = await collect("resting_heart_rate", lambda: _daily(api.daily_resting_heart_rate, since))
    if rhr:
        add(
            GOOGLE_HEALTH_DOMAIN,
            "resting_heart_rate",
            "bpm",
            _mean_by_day((_civil_date(p.date), p.beats_per_minute) for p in rhr),
        )

    # Body measurements: several a day possible, so average per local day.
    weights = await collect("weight", lambda: _newest_first(api.weight, since_dt, _sample_time))
    if weights:
        add(
            GOOGLE_HEALTH_DOMAIN,
            "weight",
            UnitOfMass.KILOGRAMS,
            _mean_by_day((_local_day(_sample_time(p), tz), p.weight_grams / 1000) for p in weights),
        )
    fat = await collect("body_fat", lambda: _newest_first(api.body_fat, since_dt, _sample_time))
    if fat:
        add(
            GOOGLE_HEALTH_DOMAIN,
            "body_fat",
            PERCENTAGE,
            _mean_by_day((_local_day(_sample_time(p), tz), p.percentage) for p in fat),
        )

    # Sleep: one main session per wake-up day.
    sessions = await collect(
        "sleep", lambda: _newest_first(api.sleep, since_dt, lambda s: parse_time(s.end_time))
    )
    if sessions:
        nights = sleep_by_wake_day(sessions, tz)

        def per_night(fn: Callable[[Sleep], int | None]) -> dict[date, float]:
            return {day: float(v) for day, s in nights.items() if (v := fn(s)) is not None}

        summary = lambda field_name: lambda s: getattr(s.summary, field_name)  # noqa: E731
        minutes = UnitOfTime.MINUTES
        for integration, key, fn in (
            (DOMAIN, "time_asleep", summary("minutes_asleep")),
            (DOMAIN, "time_in_bed", summary("minutes_in_sleep_period")),
            (DOMAIN, "deep_sleep", lambda s: stage_minutes(s, "DEEP")),
            (DOMAIN, "light_sleep", lambda s: stage_minutes(s, "LIGHT")),
            (DOMAIN, "rem_sleep", lambda s: stage_minutes(s, "REM")),
            (GOOGLE_HEALTH_DOMAIN, "sleep_asleep", summary("minutes_asleep")),
            (GOOGLE_HEALTH_DOMAIN, "sleep_in_bed", summary("minutes_in_sleep_period")),
            (GOOGLE_HEALTH_DOMAIN, "sleep_awake", summary("minutes_awake")),
            (GOOGLE_HEALTH_DOMAIN, "sleep_to_fall_asleep", summary("minutes_to_fall_asleep")),
            (GOOGLE_HEALTH_DOMAIN, "sleep_after_wakeup", summary("minutes_after_wake_up")),
        ):
            add(integration, key, minutes, per_night(fn))

    # Daily totals. Today is still running, so stop at yesterday.
    def by_day(points: list[Any], value_fn: Callable[[Any], float | None]) -> dict[date, float]:
        values: dict[date, float] = {}
        for point in points:
            day = _civil_date(point.civil_start_time.date) if point.civil_start_time else None
            if day is not None and (value := value_fn(point.data)) is not None:
                values[day] = float(value)
        return values

    # Steps go first over the whole range: the first day with steps is when a
    # tracker started recording, so the other totals only need fetching from
    # there instead of across years of empty requests.
    first_day = since
    steps = await collect("steps", lambda: _rollups(api.steps, since, today))
    if steps:
        step_values = by_day(steps, lambda v: v.count_sum)
        add(GOOGLE_HEALTH_DOMAIN, "steps", None, step_values, cumulative=True)
        if active := [day for day, value in step_values.items() if value > 0]:
            first_day = min(active)

    def energy_kcal(v: Any) -> float | None:
        return v.energy.kcal_sum if v.energy else None

    def water_liters(v: Any) -> float | None:
        return v.amount_consumed.milliliters_sum / 1000 if v.amount_consumed else None

    rollup_specs: list[tuple[str, Any, str | None, Callable[[Any], float | None], int]] = [
        (
            "distance",
            api.distance,
            UnitOfLength.METERS,
            lambda v: v.millimeters_sum / 1000,
            ROLLUP_CHUNK_DAYS,
        ),
        (
            "active_calories",
            api.active_energy_burned,
            UnitOfEnergy.KILO_CALORIE,
            lambda v: v.kcal_sum,
            ROLLUP_CHUNK_DAYS,
        ),
        (
            "total_calories",
            api.total_calories,
            UnitOfEnergy.KILO_CALORIE,
            lambda v: v.kcal_sum,
            SHORT_ROLLUP_CHUNK_DAYS,
        ),
        ("floors", api.floors, None, lambda v: v.count_sum, ROLLUP_CHUNK_DAYS),
        ("hydration", api.hydration_log, UnitOfVolume.LITERS, water_liters, ROLLUP_CHUNK_DAYS),
        (
            "calories_consumed",
            api.nutrition_log,
            UnitOfEnergy.KILO_CALORIE,
            energy_kcal,
            ROLLUP_CHUNK_DAYS,
        ),
    ]
    for key, sub_api, unit, value_fn, chunk in rollup_specs:
        points = await collect(key, lambda s=sub_api, c=chunk: _rollups(s, first_day, today, c))
        if points:
            add(GOOGLE_HEALTH_DOMAIN, key, unit, by_day(points, value_fn), cumulative=True)

    return series, unavailable


def row_start(day: date, tz: ZoneInfo) -> datetime:
    """Return the statistics hour for a day: local midnight, on a UTC hour.

    Statistics rows must start on a whole hour; in zones with a half-hour
    offset local midnight isn't one, so round down.
    """
    start = datetime.combine(day, time(), tzinfo=tz).astimezone(dt_util.UTC)
    return start.replace(minute=0, second=0, microsecond=0)


def cumulative_rows(
    values: dict[date, float], tz: ZoneInfo, before: datetime
) -> list[StatisticData]:
    """Build sum rows for daily totals, counting up from zero.

    Each day's row at local midnight carries that day's total as its change.
    Starting at zero matters: a chart treats the very first row's sum as its
    change, so a negative start would show as a large negative day.
    """
    # A day that already has recorded rows is counted by them, so stop the day
    # before the first recorded row.
    first_day = before.astimezone(tz).date()
    rows: list[StatisticData] = []
    running = 0.0
    for day in sorted(day for day in values if day < first_day):
        running += values[day]
        rows.append(StatisticData(start=row_start(day, tz), state=values[day], sum=running))
    return rows


def recorded_sum_adjustment(rows: list[StatisticData], first: dict[str, Any]) -> float:
    """Return how far to shift the recorded sums so they continue the import.

    The first recorded row's change should stay what it counted on its own:
    the day's total up to then, which is its state.
    """
    imported_total = rows[-1]["sum"] if rows else 0.0
    wanted = imported_total + (first.get("state") or 0.0)
    return wanted - (first.get("sum") or 0.0)


def mean_rows(values: dict[date, float], tz: ZoneInfo, before: datetime) -> list[StatisticData]:
    """Build one mean/min/max row per day at local midnight."""
    rows: list[StatisticData] = []
    for day in sorted(values):
        start = row_start(day, tz)
        if start >= before:
            continue
        value = values[day]
        rows.append(StatisticData(start=start, mean=value, min=value, max=value))
    return rows


def _convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    # Counts like steps have no unit here but a label ("steps") in statistics.
    if from_unit == to_unit or from_unit is None:
        return value
    converter = STATISTIC_UNIT_TO_UNIT_CONVERTER.get(from_unit)
    if converter is None or to_unit not in converter.VALID_UNITS:
        raise ValueError(f"Can't convert {from_unit} to {to_unit}")
    return converter.convert(value, from_unit, to_unit)


def _first_recorded_row(
    hass: HomeAssistant, statistic_id: str, since: datetime
) -> dict[str, Any] | None:
    """Return the first hourly row at or after `since`, the entity's creation."""
    stats = statistics_during_period(
        hass, since, None, {statistic_id}, "hour", None, {"state", "sum", "mean"}
    )
    rows = stats.get(statistic_id)
    return rows[0] if rows else None


async def async_import_history(
    hass: HomeAssistant,
    api: GoogleHealthApi,
    entity_unique_ids: dict[str, str],
    days: int,
) -> ImportResult:
    """Fetch history and import it for every entity that has statistics."""
    tz = ZoneInfo(hass.config.time_zone)
    result = ImportResult()
    try:
        series_list, unavailable = await fetch_history(api, days, tz)
    except GoogleHealthApiError as err:
        raise RuntimeError(f"Google Health API error: {err}") from err
    for name, reason in unavailable.items():
        result.skipped[name] = reason

    registry = er.async_get(hass)
    recorder = get_instance(hass)

    for series in series_list:
        unique_id = f"{entity_unique_ids[series.integration]}_{series.key}"
        entity_id = registry.async_get_entity_id("sensor", series.integration, unique_id)
        entry = registry.async_get(entity_id) if entity_id else None
        if entity_id is None or entry is None:
            result.skipped[f"{series.integration}.{series.key}"] = "no such entity"
            continue
        if not series.values:
            result.skipped[entity_id] = "no data"
            continue

        metadata = (
            await recorder.async_add_executor_job(
                partial(get_metadata, hass, statistic_ids={entity_id})
            )
        ).get(entity_id)
        if metadata is None:
            result.skipped[entity_id] = "no statistics yet; try again in an hour"
            continue
        meta = metadata[1]
        # Rows from before the entity existed can only be imported ones, so the
        # first row after its creation is the first one Home Assistant recorded.
        # That keeps a re-run from mistaking earlier imports for recordings.
        created = entry.created_at.replace(minute=0, second=0, microsecond=0)
        first = await recorder.async_add_executor_job(_first_recorded_row, hass, entity_id, created)
        before = dt_util.utc_from_timestamp(first["start"]) if first else dt_util.utcnow()
        unit = meta["unit_of_measurement"]
        try:
            values = {
                day: _convert(value, series.unit, unit) for day, value in series.values.items()
            }
        except ValueError as err:
            result.skipped[entity_id] = str(err)
            continue

        if series.cumulative:
            if not meta["has_sum"]:
                result.skipped[entity_id] = "statistics have no sum"
                continue
            rows = cumulative_rows(values, tz, before)
        else:
            rows = mean_rows(values, tz, before)

        if not rows:
            result.skipped[entity_id] = "nothing older than what is already recorded"
            continue
        async_import_statistics(hass, meta, rows)
        if series.cumulative and first:
            # Queued after the import, so it runs once the rows are in.
            adjustment = recorded_sum_adjustment(rows, first)
            if abs(adjustment) > 1e-6:
                # Same unit as the statistics, so no conversion happens.
                recorder.async_adjust_statistics(entity_id, before, adjustment, unit)  # type: ignore[arg-type]
        result.imported[entity_id] = len(rows)
        _LOGGER.debug("Imported %s days for %s", len(rows), entity_id)

    return result
