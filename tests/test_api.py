"""Client of the web service: parsing, verification and errors."""

from __future__ import annotations

import traceback
from datetime import UTC, date, datetime

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.siar import api

from .conftest import PASSWORD, USER, encrypted


def client(hass, password: str = PASSWORD) -> api.SiarClient:
    return api.SiarClient(async_get_clientsession(hass), USER, password)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("391520000N", 39.255556),
        ("015512000W", -1.92),
        ("015512000O", -1.92),
        ("401000000N", 40.166667),
        ("", None),
        (None, None),
        ("396120000N", None),
        ("abc", None),
    ],
)
def test_parse_dms(text, expected):
    assert api.parse_dms(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        ("1,5", 1.5),
        (-5.2, -5.2),
        (-999, None),
        (-6999, None),
        (-99999, None),
        ("", None),
        (None, None),
        (True, None),
        ("nan", None),
    ],
)
def test_number(value, expected):
    assert api.number(value) == expected


def test_decode_falls_back():
    assert api.decode("límite".encode("latin-1")) == "límite"
    assert api.decode("límite".encode(), "no-such-charset") == "límite"


def test_end_of_day_is_next_midnight():
    rows = {"datos": [{"Fecha": "2026-05-09T00:00:00", "Estacion": "xx01", "HorMin": 2400, "TempMedia": 1}]}
    [reading] = api.parse_readings(rows, hourly=True)
    assert reading.station == "XX01"
    assert reading.time == datetime(2026, 5, 10, tzinfo=UTC)


def test_bad_rows_are_skipped():
    rows = {
        "datos": [
            {"Fecha": "nunca", "Estacion": "XX01", "HorMin": 30, "TempMedia": 1},
            {"Fecha": "2026-05-09", "Estacion": "XX01", "HorMin": 2430, "TempMedia": 1},
            {"Fecha": "2026-05-09", "Estacion": "XX01", "HorMin": 30, "TempMedia": -6999},
            {"Fecha": "09/05/2026", "Estacion": "XX01", "HorMin": "130", "TempMedia": 2},
        ]
    }
    assert [r.time for r in api.parse_readings(rows, hourly=True)] == [datetime(2026, 5, 9, 1, 30, tzinfo=UTC)]


def test_empty_answers():
    assert api.records({"datos": [], "MensajeRespuesta": "No hay datos"}) == []
    with pytest.raises(api.SiarRateLimitError):
        api.records({"datos": [], "MensajeRespuesta": "Ha superado el límite de registros"})
    with pytest.raises(api.SiarResponseError):
        api.records({"datos": None, "MensajeRespuesta": "Fallo interno"})
    with pytest.raises(api.SiarResponseError):
        api.records("<html></html>")


async def test_login_is_verified_with_a_real_request(hass, siar):
    one = client(hass)
    await one.login()
    assert [path for path, _ in siar.calls] == [
        "Autenticacion/cifrarCadena",
        "Autenticacion/cifrarCadena",
        "Autenticacion/obtenerToken",
        "Info/ACCESOS",
    ]
    assert dict(siar.calls[2][1]) == {"Usuario": encrypted(USER), "Password": encrypted(PASSWORD)}
    assert one.accesses["maxaccesosdia"] == 1000


async def test_token_alone_proves_nothing(hass, siar):
    with pytest.raises(api.SiarAuthError) as err:
        await client(hass, "otra").login()
    assert not err.value.definitive
    assert siar.count("Info/ACCESOS") == 1


async def test_accesses_without_limits_is_not_a_login(hass, siar):
    siar.fail["Info/ACCESOS"] = (200, {"datos": [], "MensajeRespuesta": None})
    with pytest.raises(api.SiarResponseError):
        await client(hass).login()


@pytest.mark.parametrize(
    ("path", "answer", "error"),
    [
        ("Info/ACCESOS", (401, ""), api.SiarAuthError),
        ("Info/ACCESOS", (403, {"MensajeRespuesta": "Usuario no autorizado"}), api.SiarAuthError),
        ("Info/ACCESOS", (403, {"MensajeRespuesta": "Límite de peticiones superado"}), api.SiarRateLimitError),
        ("cifrarCadena", (429, ""), api.SiarRateLimitError),
        ("cifrarCadena", (500, ""), api.SiarConnectionError),
        ("cifrarCadena", (503, "<html>mantenimiento</html>"), api.SiarConnectionError),
        ("cifrarCadena", (302, ""), api.SiarResponseError),
        ("cifrarCadena", (404, ""), api.SiarResponseError),
        ("cifrarCadena", (200, "<html>portal</html>"), api.SiarResponseError),
        ("obtenerToken", (200, "Usuario no registrado"), api.SiarAuthError),
        ("obtenerToken", (200, ""), api.SiarAuthError),
    ],
)
async def test_refusals(hass, siar, path, answer, error):
    siar.fail[path] = answer
    with pytest.raises(error) as err:
        await client(hass).login()
    assert type(err.value) is error


async def test_secrets_never_reach_the_error(hass, siar):
    siar.fail["Info/ACCESOS"] = (403, {"MensajeRespuesta": f"No vale {USER} {PASSWORD} {siar.good_token}"})
    with pytest.raises(api.SiarAuthError) as err:
        await client(hass).login()
    text = str(err.value)
    assert "***" in text
    assert not any(secret in text for secret in (USER, PASSWORD, siar.good_token))


@pytest.mark.parametrize("failure", [aiohttp.ClientConnectionError(f"https://x/?cadena={PASSWORD}"), TimeoutError()])
async def test_network_errors_hide_the_url(hass, siar, failure):
    siar.exception = failure
    with pytest.raises(api.SiarConnectionError) as err:
        await client(hass).login()
    assert PASSWORD not in "".join(traceback.format_exception(err.value))


async def test_expired_token_logs_in_again(hass, siar):
    one = client(hass)
    await one.login()
    siar.generation += 1
    assert len(await one.stations()) == 3
    assert siar.count("obtenerToken") == 2


async def test_server_failure_with_verified_token_is_not_auth(hass, siar):
    siar.fail["Datos/"] = (500, "")
    with pytest.raises(api.SiarConnectionError):
        await client(hass).readings(["XX01"], date(2026, 5, 9), date(2026, 5, 10), hourly=True)


async def test_stations_in_batches(hass, siar):
    codes = [f"YY{n:02d}" for n in range(23)]
    await client(hass).readings(codes, date(2026, 5, 9), date(2026, 5, 10), hourly=False)
    batches = [[v for k, v in query if k == "Id"] for path, query in siar.calls if path.startswith("Datos/")]
    assert [len(batch) for batch in batches] == [10, 10, 3]
    assert sorted(sum(batches, [])) == codes
    assert ("DatosCalculados", "true") in siar.calls[-1][1]


def test_stations_closed_and_repeated():
    payload = {
        "datos": [
            {"Codigo": "xx09", "Estacion": "Nueva", "Fecha_Baja": None},
            {"Codigo": "XX09", "Estacion": "Vieja", "Fecha_Baja": "2001-01-01"},
            {"Codigo": "", "Estacion": "Sin código"},
        ]
    }
    [only] = api.parse_stations(payload)
    assert (only.code, only.name, only.closed) == ("XX09", "Nueva", False)


async def test_real_http_encoding_and_no_redirects(socket_enabled):
    """A real server: special characters arrive intact and redirects are not followed."""
    seen: list[tuple[str, dict[str, str]]] = []

    async def handle(request: web.Request) -> web.StreamResponse:
        seen.append((request.path, dict(request.query)))
        if request.path.endswith("cifrarCadena"):
            return web.Response(text=encrypted(request.query["cadena"]))
        if request.path.endswith("obtenerToken"):
            return web.Response(text='"abc+/=&"')
        if request.path.endswith("ACCESOS"):
            return web.Response(body='{"datos":[{"MaxAccesosDia":1000}]}'.encode("latin-1"), content_type="application/json")
        raise web.HTTPFound("http://127.0.0.1:1/robo?token=x")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        one = api.SiarClient(session, USER, PASSWORD, str(server.make_url("/siarapi/API/V1")))
        await one.login()
        with pytest.raises(api.SiarResponseError):
            await one.stations()
    assert seen[0][1] == {"cadena": USER}
    assert seen[1][1] == {"cadena": PASSWORD}
    assert seen[2][1] == {"Usuario": encrypted(USER), "Password": encrypted(PASSWORD)}
    assert seen[3][1] == {"token": "abc+/=&"}
