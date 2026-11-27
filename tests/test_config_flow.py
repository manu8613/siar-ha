"""Set-up, re-authentication and options."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr

from custom_components.siar.const import CONF_INTERVAL, CONF_STATIONS, DOMAIN

from .conftest import PASSWORD, USER, make_entry

GOOD = {CONF_USERNAME: f" {USER.lower()} ", CONF_PASSWORD: PASSWORD}


async def start(hass, user_input=GOOD):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


def choices(result) -> list[dict[str, str]]:
    return next(v for k, v in result["data_schema"].schema.items() if k == CONF_STATIONS).config["options"]


async def test_full_set_up(hass, siar):
    result = await start(hass)
    assert result["step_id"] == "stations"
    # nearest first, the closed station is not offered
    assert [c["value"] for c in choices(result)] == ["XX01", "XX02"]
    assert choices(result)[0]["label"] == "XX01 · Estación Alfa (Villa Ejemplo) — 2 km"
    assert result["data_schema"]({})[CONF_STATIONS] == ["XX01"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STATIONS: ["XX02", "XX01"]})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}
    assert list(result["options"][CONF_STATIONS]) == ["XX01", "XX02"]
    assert result["options"][CONF_STATIONS]["XX02"]["latitude"] == 42.0
    entry = result["result"]
    assert USER not in entry.unique_id and USER not in entry.title
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("break_it", "error"),
    [
        (lambda s: setattr(s, "password", "otra"), "invalid_auth"),
        (lambda s: setattr(s, "exception", aiohttp.ClientConnectionError()), "cannot_connect"),
        (lambda s: s.fail.update({"ACCESOS": (429, "")}), "rate_limit"),
        (lambda s: s.fail.update({"ESTACIONES": (200, {"datos": []})}), "unexpected_response"),
        (lambda s: setattr(s, "exception", RuntimeError()), "unknown"),
    ],
)
async def test_credentials_are_checked_and_can_be_retried(hass, siar, break_it, error):
    break_it(siar)
    result = await start(hass)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert PASSWORD not in str(result["description_placeholders"])
    assert not hass.config_entries.async_entries(DOMAIN)

    siar.password, siar.exception = PASSWORD, None
    siar.fail.clear()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], GOOD)
    assert result["step_id"] == "stations"


async def test_stations_are_checked(hass, siar):
    siar.silent = {"XX02"}
    result = await start(hass)
    flow = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow, {CONF_STATIONS: []})
    assert result["errors"] == {CONF_STATIONS: "no_stations"}

    siar.fail["Horarios"] = (503, "")
    result = await hass.config_entries.flow.async_configure(flow, {CONF_STATIONS: ["XX01"]})
    assert result["errors"] == {"base": "cannot_connect"}
    siar.fail.clear()

    result = await hass.config_entries.flow.async_configure(flow, {CONF_STATIONS: ["XX01", "XX02"]})
    assert result["errors"] == {CONF_STATIONS: "station_without_data"}
    assert result["description_placeholders"]["detail"] == "XX02"
    result = await hass.config_entries.flow.async_configure(flow, {CONF_STATIONS: ["XX01", "XX02"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_same_account_twice(hass, siar):
    make_entry().add_to_hass(hass)
    result = await start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(("source", "step"), [("reauth", "reauth_confirm"), ("reconfigure", "reconfigure")])
async def test_new_password(hass, siar, source, step):
    entry = make_entry(password="vieja")
    entry.add_to_hass(hass)
    starter = entry.start_reauth_flow if source == "reauth" else entry.start_reconfigure_flow
    result = await starter(hass)
    assert result["step_id"] == step
    flow = result["flow_id"]

    result = await hass.config_entries.flow.async_configure(flow, {CONF_USERNAME: USER, CONF_PASSWORD: "mala"})
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "vieja"

    result = await hass.config_entries.flow.async_configure(flow, GOOD)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == f"{source}_successful"
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert entry.state is ConfigEntryState.LOADED


async def test_new_credentials_of_another_account(hass, siar):
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id="otra-cuenta")
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], GOOD)
    assert result["reason"] == "wrong_account"


async def test_options_add_and_remove_stations(hass, siar):
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)

    def codes() -> set[str]:
        return {d.serial_number for d in dr.async_entries_for_config_entry(registry, entry.entry_id) if d.serial_number}

    assert codes() == {"XX01"}
    for chosen, interval in ((["XX01", "XX02"], 90), (["XX02"], 30)):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["step_id"] == "init"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_STATIONS: chosen, CONF_INTERVAL: interval})
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert codes() == set(chosen)
        assert entry.runtime_data.update_interval.total_seconds() == interval * 60
    assert entry.state is ConfigEntryState.LOADED


async def test_options_with_service_down(hass, siar):
    entry = make_entry()
    entry.add_to_hass(hass)
    siar.exception = aiohttp.ClientConnectionError()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_vanished_station_can_still_be_removed(hass, siar):
    entry = make_entry(("XX01", "ZZ99"))
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "ZZ99" in [c["value"] for c in choices(result)]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_STATIONS: ["XX01"], CONF_INTERVAL: 60})
    assert list(result["data"][CONF_STATIONS]) == ["XX01"]
    await hass.async_block_till_done()  # the options flow reloads the entry
