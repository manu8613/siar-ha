"""Sensors of each station and of the account."""

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
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfIrradiance,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION
from .coordinator import SiarConfigEntry, SiarCoordinator, StationData
from .entity import SiarStationEntity, account_device

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SiarSensorDescription(SensorEntityDescription):
    """A sensor of a station."""

    value: Callable[[StationData], float | datetime | None]
    source: str = "latest"  # "latest", "day", "today" or "time"
    only_if_present: bool = False  # sensor that not every station has


def _latest(key: str, **kwargs: Any) -> SiarSensorDescription:
    return SiarSensorDescription(
        key=key,
        translation_key=key,
        value=lambda s: s.latest.values.get(key) if s.latest else None,
        **kwargs,
    )


def _day(key: str, **kwargs: Any) -> SiarSensorDescription:
    return SiarSensorDescription(
        key=f"day_{key}",
        translation_key=f"day_{key}",
        value=lambda s: s.day.values.get(key) if s.day else None,
        source="day",
        **kwargs,
    )


_MEASUREMENT = SensorStateClass.MEASUREMENT
_TEMPERATURE: dict[str, Any] = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "suggested_display_precision": 1,
}
_HUMIDITY: dict[str, Any] = {
    "device_class": SensorDeviceClass.HUMIDITY,
    "native_unit_of_measurement": PERCENTAGE,
    "suggested_display_precision": 0,
}
_WIND: dict[str, Any] = {
    "device_class": SensorDeviceClass.WIND_SPEED,
    "native_unit_of_measurement": UnitOfSpeed.METERS_PER_SECOND,
    "suggested_display_precision": 1,
}
_RAIN: dict[str, Any] = {
    "device_class": SensorDeviceClass.PRECIPITATION,
    "native_unit_of_measurement": UnitOfPrecipitationDepth.MILLIMETERS,
    "suggested_display_precision": 1,
}

STATION_SENSORS: tuple[SiarSensorDescription, ...] = (
    _latest("temperature", state_class=_MEASUREMENT, **_TEMPERATURE),
    _latest("humidity", state_class=_MEASUREMENT, **_HUMIDITY),
    _latest("wind_speed", state_class=_MEASUREMENT, **_WIND),
    _latest(
        "wind_bearing",
        device_class=SensorDeviceClass.WIND_DIRECTION,
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT_ANGLE,
        suggested_display_precision=0,
    ),
    _latest(
        "irradiance",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=_MEASUREMENT,
        suggested_display_precision=0,
    ),
    # rain of one half hour: an amount, but not a running total
    _latest("precipitation", **_RAIN),
    _latest("soil_temperature_1", state_class=_MEASUREMENT, only_if_present=True, **_TEMPERATURE),
    _latest("soil_temperature_2", state_class=_MEASUREMENT, only_if_present=True, **_TEMPERATURE),
    SiarSensorDescription(
        key="rain_today",
        translation_key="rain_today",
        value=lambda s: s.rain_today,
        source="today",
        state_class=SensorStateClass.TOTAL,
        **_RAIN,
    ),
    SiarSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        value=lambda s: s.last_time,
        source="time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _day(
        "et0",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        suggested_display_precision=2,
    ),
    _day("effective_precipitation", **_RAIN),
    _day("precipitation", **_RAIN),
    _day("temperature_max", **_TEMPERATURE),
    _day("temperature_min", **_TEMPERATURE),
    _day("temperature", **_TEMPERATURE),
    _day("humidity", **_HUMIDITY),
    _day("wind_speed_max", **_WIND),
    _day("radiation", native_unit_of_measurement="MJ/m²", suggested_display_precision=2),
)


async def async_setup_entry(hass: HomeAssistant, entry: SiarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Create the sensors; soil probes only once a station gives them."""
    coordinator = entry.runtime_data
    created: set[tuple[str, str]] = set()
    async_add_entities([SiarRequestsSensor(coordinator)])

    def add_new() -> None:
        new = []
        for code in coordinator.codes:
            data = coordinator.data.stations.get(code) or StationData()
            for description in STATION_SENSORS:
                if (code, description.key) in created:
                    continue
                if description.only_if_present and description.value(data) is None:
                    continue
                created.add((code, description.key))
                new.append(SiarSensor(coordinator, code, description))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class SiarSensor(SiarStationEntity, SensorEntity):
    """One value of one station."""

    entity_description: SiarSensorDescription

    def __init__(self, coordinator: SiarCoordinator, code: str, description: SiarSensorDescription) -> None:
        super().__init__(coordinator, code)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{code}_{description.key}"

    @property
    def native_value(self) -> float | datetime | None:
        return self.entity_description.value(self.station)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.source != "today":
            return None
        return self.station.rain_since

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        source = self.entity_description.source
        if source == "latest" and self.station.latest:
            return {"hora_medida": dt_util.as_local(self.station.latest.time).isoformat()}
        if source == "day" and self.station.day:
            return {"fecha": self.station.day.time.date().isoformat()}
        return None


class SiarRequestsSensor(CoordinatorEntity[SiarCoordinator], SensorEntity):
    """Requests made today with the account, as counted by SiAR."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_translation_key = "requests_today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SiarCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_requests_today"
        self._attr_device_info = account_device(coordinator)

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data.accesses.get("numaccesosdiaactual")
        return None if value is None else int(value)

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        names = {
            "maximo_peticiones_dia": "maxaccesosdia",
            "maximo_peticiones_minuto": "maxaccesosminuto",
            "registros_hoy": "registrosacumuladosdia",
            "maximo_registros_dia": "maxregistrosdia",
        }
        accesses = self.coordinator.data.accesses
        return {name: int(value) for name, key in names.items() if (value := accesses.get(key)) is not None}
