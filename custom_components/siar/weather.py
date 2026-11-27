"""One weather entity per station: observations only, SiAR gives no forecast."""

from __future__ import annotations

from typing import Any

from homeassistant.components.weather import WeatherEntity
from homeassistant.const import UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import SiarConfigEntry, SiarCoordinator
from .entity import SiarStationEntity
from .sky import condition

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: SiarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Create the weather entities."""
    coordinator = entry.runtime_data
    async_add_entities(SiarWeather(coordinator, code) for code in coordinator.codes)


class SiarWeather(SiarStationEntity, WeatherEntity):
    """Latest half hour of a station."""

    _attr_name = None  # the entity is the station itself
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND

    def __init__(self, coordinator: SiarCoordinator, code: str) -> None:
        super().__init__(coordinator, code)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{code}_weather"

    def _value(self, key: str) -> float | None:
        latest = self.station.latest
        return latest.values.get(key) if latest else None

    @property
    def available(self) -> bool:
        return super().available and self.station.latest is not None

    @property
    def condition(self) -> str | None:
        latest = self.station.latest
        if latest is None:
            return None
        return condition(latest.values, latest.time, self.info.get("latitude"), self.info.get("longitude"))

    @property
    def native_temperature(self) -> float | None:
        return self._value("temperature")

    @property
    def humidity(self) -> float | None:
        return self._value("humidity")

    @property
    def native_wind_speed(self) -> float | None:
        return self._value("wind_speed")

    @property
    def wind_bearing(self) -> float | None:
        return self._value("wind_bearing")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        latest = self.station.latest
        if latest is None:
            return None
        return {
            "hora_medida": dt_util.as_local(latest.time).isoformat(),
            "condicion_estimada": True,
        }
