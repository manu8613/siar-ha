"""Estimate of the condition."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.siar import sky

NOON = datetime(2026, 3, 20, 12, 15, tzinfo=UTC)
NIGHT = datetime(2026, 3, 20, 1, 0, tzinfo=UTC)


def test_sun_at_the_equinox():
    assert sky.sun_elevation(datetime(2026, 3, 20, 12, 7, tzinfo=UTC), 40.0, 0.0) == pytest.approx(50.0, abs=0.5)
    assert sky.sun_elevation(NIGHT, 40.0, 0.0) < 0
    assert sky.clear_sky_irradiance(-5) == 0
    assert 750 < sky.clear_sky_irradiance(50) < 800


@pytest.mark.parametrize(
    ("values", "when", "expected"),
    [
        ({"irradiance": 760}, NOON, "sunny"),
        ({"irradiance": 450}, NOON, "partlycloudy"),
        ({"irradiance": 100}, NOON, "cloudy"),
        ({"irradiance": 760, "precipitation": 0.2}, NOON, "rainy"),
        ({"precipitation": 5.0}, NIGHT, "pouring"),
        ({"precipitation": 0.4, "temperature": -1.0}, NIGHT, "snowy"),
        ({"wind_speed": 12.0, "irradiance": 760}, NOON, "windy"),
        ({"irradiance": 0, "wind_speed": 1.0}, NIGHT, None),
        ({}, NOON, None),
    ],
)
def test_condition(values, when, expected):
    assert sky.condition(values, when, 40.0, 0.0) == expected


def test_condition_without_position():
    assert sky.condition({"irradiance": 760}, NOON, None, None) is None
