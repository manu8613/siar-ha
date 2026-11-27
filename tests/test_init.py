"""Entities, refreshes and failures."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.siar.const import DOMAIN
from custom_components.siar.diagnostics import async_get_config_entry_diagnostics

from .conftest import PASSWORD, USER, make_entry


async def setup(hass, codes=("XX01",)):
    entry = make_entry(codes)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity(hass, entry, key: str, platform: str = "sensor", code: str | None = "XX01"):
    unique = f"{entry.entry_id}_{code}_{key}" if code else f"{entry.entry_id}_{key}"
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique)


def state(hass, entry, key: str, platform: str = "sensor", code: str | None = "XX01"):
    entity_id = entity(hass, entry, key, platform, code)
    return hass.states.get(entity_id) if entity_id else None


async def tick(hass, freezer, minutes: int = 61):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_values(hass, siar):
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    expected = {
        "temperature": "18.4",
        "humidity": "55.0",
        "wind_bearing": "270.0",
        "irradiance": "800.0",
        "precipitation": "0.0",
        "soil_temperature_1": "15.2",
        # half hours after local midnight (22:00 UTC) up to 11:30 UTC; the one of 12:00 is dry
        "rain_today": "5.4",
        "last_reading": "2026-05-10T12:00:00+00:00",
        "day_et0": "4.12",
        "day_effective_precipitation": "0.9",
        "day_temperature_max": "24.3",
        "day_radiation": "24.5",
    }
    for key, value in expected.items():
        assert state(hass, entry, key).state == value, key
    assert float(state(hass, entry, "wind_speed").state) in (2.5, 9.0)  # m/s, or km/h on metric systems
    assert state(hass, entry, "day_et0").attributes["fecha"] == "2026-05-09"
    assert state(hass, entry, "rain_today").attributes["last_reset"] == "2026-05-10T00:00:00+02:00"
    assert state(hass, entry, "soil_temperature_2") is None
    assert state(hass, entry, "requests_today", code=None).state == "12"

    weather = state(hass, entry, "weather", "weather")
    assert weather.state == "sunny"
    assert weather.attributes["temperature"] == 18.4
    assert weather.attributes["wind_bearing"] == 270
    assert weather.attributes["condicion_estimada"] is True
    assert "SiAR" in weather.attributes["attribution"]


async def test_several_stations_and_devices(hass, siar):
    entry = await setup(hass, ("XX01", "XX02"))
    assert state(hass, entry, "temperature", code="XX02").state == "18.4"
    assert state(hass, entry, "soil_temperature_1", code="XX02") is None
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    account = next(d for d in devices if d.serial_number is None)
    assert [d.via_device_id for d in devices if d.serial_number] == [account.id, account.id]


async def test_soil_probe_that_appears_later(hass, siar, freezer):
    siar.soil = set()
    entry = await setup(hass)
    assert state(hass, entry, "soil_temperature_1") is None
    siar.soil = {"XX01"}
    await tick(hass, freezer)
    assert state(hass, entry, "soil_temperature_1").state == "15.2"


async def test_few_requests_per_refresh(hass, siar, freezer):
    await setup(hass, ("XX01", "XX02"))
    siar.calls.clear()
    await tick(hass, freezer)
    assert [path for path, _ in siar.calls] == ["Datos/Horarios/ESTACION", "Info/ACCESOS"]
    siar.calls.clear()
    await tick(hass, freezer, 181)
    assert siar.count("Diarios") == 1
    assert siar.count("obtenerToken") == 0


async def test_old_records_are_not_current(hass, siar, freezer):
    entry = await setup(hass)
    siar.silent = {"XX01"}
    await tick(hass, freezer)
    assert state(hass, entry, "temperature").state == "unavailable"
    assert state(hass, entry, "weather", "weather").state == "unavailable"


async def test_station_that_stopped_days_ago(hass, siar):
    original = siar._hourly
    siar._hourly = lambda *args: [r for r in original(*args) if r["Fecha"] < "2026-05-08"]
    entry = await setup(hass)
    assert state(hass, entry, "temperature").state == "unavailable"


async def test_service_down_then_back(hass, siar, freezer):
    entry = await setup(hass)
    siar.exception = aiohttp.ClientConnectionError()
    await tick(hass, freezer)
    assert state(hass, entry, "temperature").state == "unavailable"
    assert not hass.config_entries.flow.async_progress()
    siar.exception = None
    await tick(hass, freezer)
    assert state(hass, entry, "temperature").state == "18.4"


async def test_service_down_at_start(hass, siar):
    siar.exception = aiohttp.ClientConnectionError()
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_refused_credentials_ask_again(hass, siar):
    siar.fail["ACCESOS"] = (401, "")
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_ambiguous_refusal_waits_three_times(hass, siar, freezer):
    entry = await setup(hass)
    siar.password = "cambiada"
    siar.generation += 1  # the token expires and the old password no longer works
    for _ in range(2):
        await tick(hass, freezer)
        assert not hass.config_entries.flow.async_progress()
    await tick(hass, freezer)
    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert entry.state is ConfigEntryState.LOADED


async def test_limits_are_optional(hass, siar, freezer):
    entry = await setup(hass)
    siar.fail["ACCESOS"] = (503, "")
    await tick(hass, freezer)
    assert state(hass, entry, "temperature").state == "18.4"
    assert state(hass, entry, "requests_today", code=None).state == "unknown"


async def test_diagnostics_and_logs_without_secrets(hass, siar, freezer, caplog):
    caplog.set_level("DEBUG")
    entry = await setup(hass)
    siar.exception = aiohttp.ClientConnectionError(f"url?cadena={PASSWORD}")
    await tick(hass, freezer)
    siar.exception = None
    await tick(hass, freezer)
    report = str(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (USER, PASSWORD, siar.good_token):
        assert secret not in report
        assert secret not in "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("custom_components.siar"))
    assert PASSWORD not in caplog.text


async def test_unload(hass, siar):
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
