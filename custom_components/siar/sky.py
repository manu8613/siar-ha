"""Estimate of the weather condition from what a station measures (pure Python).

A SiAR station has no sensor for clouds, so the condition of the weather entity
is an estimate: rain from the rain gauge, wind from the anemometer and, with the
sun high enough, the measured irradiance against that of a clear sky. When none
of that says anything (a calm, dry night) the condition is unknown.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime

MIN_ELEVATION = 10.0  # °, below this irradiance says little about clouds
WINDY = 10.8  # m/s, Beaufort 6
POURING = 3.8  # mm in half an hour (7.6 mm/h, "heavy rain")


def sun_elevation(when: datetime, latitude: float, longitude: float) -> float:
    """Elevation of the sun in degrees (NOAA short formulas)."""
    when = when.astimezone(UTC)
    day = when.timetuple().tm_yday
    minutes = when.hour * 60 + when.minute + when.second / 60
    g = 2 * math.pi / 365 * (day - 1 + (minutes / 60 - 12) / 24)
    equation = 229.18 * (
        0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    hour_angle = math.radians((minutes + equation + 4 * longitude) / 4 - 180)
    phi = math.radians(latitude)
    cosine = math.sin(phi) * math.sin(declination) + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def clear_sky_irradiance(elevation: float) -> float:
    """Global horizontal irradiance of a clear sky (Haurwitz), W/m²."""
    sine = math.sin(math.radians(elevation))
    return 0.0 if sine <= 0 else 1098 * sine * math.exp(-0.057 / sine)


def condition(values: Mapping[str, float | None], end: datetime, latitude: float | None, longitude: float | None) -> str | None:
    """Condition of the half hour ending at ``end``, with Home Assistant's names."""
    rain = values.get("precipitation")
    temperature = values.get("temperature")
    if rain:
        if temperature is not None and temperature <= 0.5:
            return "snowy"
        return "pouring" if rain >= POURING else "rainy"
    wind = values.get("wind_speed")
    if wind is not None and wind >= WINDY:
        return "windy"
    irradiance = values.get("irradiance")
    if irradiance is None or latitude is None or longitude is None:
        return None
    middle = datetime.fromtimestamp(end.timestamp() - 900, UTC)
    elevation = sun_elevation(middle, latitude, longitude)
    if elevation < MIN_ELEVATION:
        return None
    ratio = irradiance / clear_sky_irradiance(elevation)
    if ratio >= 0.75:
        return "sunny"
    return "partlycloudy" if ratio >= 0.4 else "cloudy"
