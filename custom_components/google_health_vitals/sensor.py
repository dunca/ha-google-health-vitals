"""Sensors for Google Health Vitals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import VitalsConfigEntry
from .const import DOMAIN, GOOGLE_HEALTH_DOMAIN
from .coordinator import VitalsCoordinator, VitalsData
from .selection import parse_time, stage_minutes


def _date(payload: Any) -> str | None:
    """Return the calendar date a daily value belongs to."""
    date = getattr(payload, "date", None)
    if date is None or date.year is None:
        return None
    return f"{date.year:04d}-{date.month or 1:02d}-{date.day or 1:02d}"


def _skin_temperature_variation(data: VitalsData) -> float | None:
    temp = data.skin_temperature
    if temp is None or temp.baseline_temperature_celsius is None:
        return None
    return round(temp.nightly_temperature_celsius - temp.baseline_temperature_celsius, 2)


@dataclass(frozen=True, kw_only=True)
class VitalsSensorDescription(SensorEntityDescription):
    """Describe a vitals sensor."""

    value_fn: Callable[[VitalsData], float | int | datetime | None]
    attrs_fn: Callable[[VitalsData], dict[str, Any]] | None = None


SENSORS: tuple[VitalsSensorDescription, ...] = (
    VitalsSensorDescription(
        key="oxygen_saturation",
        translation_key="oxygen_saturation",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.oxygen_saturation and d.oxygen_saturation.average_percentage,
        attrs_fn=lambda d: (
            {
                "date": _date(d.oxygen_saturation),
                "lower_bound": d.oxygen_saturation.lower_bound_percentage,
                "upper_bound": d.oxygen_saturation.upper_bound_percentage,
            }
            if d.oxygen_saturation
            else {}
        ),
    ),
    VitalsSensorDescription(
        key="heart_rate_variability",
        translation_key="heart_rate_variability",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: (
            d.heart_rate_variability
            and d.heart_rate_variability.average_heart_rate_variability_milliseconds
        ),
        attrs_fn=lambda d: (
            {
                "date": _date(d.heart_rate_variability),
                "deep_sleep_rmssd": d.heart_rate_variability.deep_sleep_root_mean_square_of_successive_differences_milliseconds,
                "non_rem_heart_rate": d.heart_rate_variability.non_rem_heart_rate_beats_per_minute,
            }
            if d.heart_rate_variability
            else {}
        ),
    ),
    VitalsSensorDescription(
        key="respiratory_rate",
        translation_key="respiratory_rate",
        native_unit_of_measurement="br/min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.respiratory_rate and d.respiratory_rate.breaths_per_minute,
        attrs_fn=lambda d: {"date": _date(d.respiratory_rate)} if d.respiratory_rate else {},
    ),
    VitalsSensorDescription(
        key="skin_temperature_variation",
        translation_key="skin_temperature_variation",
        # A difference, not a temperature: no device class, so it is never
        # converted to Fahrenheit as if it were an absolute reading.
        native_unit_of_measurement="°C",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_skin_temperature_variation,
        attrs_fn=lambda d: (
            {
                "date": _date(d.skin_temperature),
                "nightly": d.skin_temperature.nightly_temperature_celsius,
                "baseline": d.skin_temperature.baseline_temperature_celsius,
            }
            if d.skin_temperature
            else {}
        ),
    ),
    VitalsSensorDescription(
        key="vo2_max",
        translation_key="vo2_max",
        native_unit_of_measurement="mL/kg/min",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.vo2_max and d.vo2_max.vo2_max,
        attrs_fn=lambda d: (
            {
                "date": _date(d.vo2_max),
                "fitness_level": d.vo2_max.cardio_fitness_level,
                "estimated": d.vo2_max.estimated,
            }
            if d.vo2_max
            else {}
        ),
    ),
    *(
        VitalsSensorDescription(
            key=f"{stage.lower()}_sleep",
            translation_key=f"{stage.lower()}_sleep",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda d, stage=stage: stage_minutes(d.sleep, stage),
        )
        for stage in ("DEEP", "LIGHT", "REM")
    ),
    VitalsSensorDescription(
        key="bedtime",
        translation_key="bedtime",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.sleep and parse_time(d.sleep.start_time),
    ),
    VitalsSensorDescription(
        key="wake_time",
        translation_key="wake_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.sleep and parse_time(d.sleep.end_time),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VitalsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    source = coordinator.source
    # Hang this device under the Google Health account device, the way that
    # integration shows its trackers.
    account = dr.async_get(hass).async_get_device_by_identifier(
        (GOOGLE_HEALTH_DOMAIN, source.entry_id), source.entry_id
    )
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"{source.title} vitals",
        manufacturer="Google",
        entry_type=DeviceEntryType.SERVICE,
    )
    if account is not None:
        device_info["via_device_id"] = account.id
    async_add_entities(
        VitalsSensor(coordinator, description, device_info) for description in SENSORS
    )


class VitalsSensor(CoordinatorEntity[VitalsCoordinator], SensorEntity):
    """A sensor reading one metric from the coordinator."""

    _attr_has_entity_name = True
    entity_description: VitalsSensorDescription

    def __init__(
        self,
        coordinator: VitalsCoordinator,
        description: VitalsSensorDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        source = coordinator.source
        self._attr_unique_id = f"{source.unique_id or source.entry_id}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | int | datetime | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the date and details behind the value."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data) or None
