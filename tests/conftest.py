"""A pretend SiAR service. Stations, places and credentials are all invented."""

from __future__ import annotations

import base64
import re
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.siar.config_flow import account_id
from custom_components.siar.const import CONF_INTERVAL, CONF_STATIONS, DOMAIN

USER = "00000000T"
PASSWORD = "Inventada+/=&?# 1"
NOW = "2026-05-10T12:10:00+00:00"
ACCESSES = {
    "NumAccesosMinutoActual": 3,
    "MaxAccesosMinuto": 30,
    "NumAccesosDiaActual": 12,
    "MaxAccesosDia": 1000,
    "RegistrosAcumuladosMinuto": 100,
    "MaxRegistrosMinuto": 1000000,
    "RegistrosAcumuladosDia": 500,
    "MaxRegistrosDia": 1000000,
}


def station(code: str, name: str, town: str, lat: str, lon: str, closed: str | None = None) -> dict[str, Any]:
    return {
        "Estacion": name,
        "Codigo": code,
        "Termino": town,
        "Latitud": lat,
        "Longitud": lon,
        "Altitud": 600,
        "Fecha_Instalacion": "2000-01-01T00:00:00",
        "Fecha_Baja": closed,
        "Red_Estacion": "SiAR",
    }


STATIONS = [
    station("XX02", "Estación Beta", "Pueblo Lejano", "420000000N", "010000000W"),
    station("XX01", "Estación Alfa", "Villa Ejemplo", "400100000N", "040000000W"),
    station("XX03", "Estación Antigua", "Villa Ejemplo", "400000000N", "040000000W", "2010-01-01T00:00:00"),
]


def encrypted(text: str) -> str:
    return "+" + base64.b64encode(text.encode()).decode() + "/="


class FakeSiar:
    """Behaves like the service, including its habit of giving a token to anyone."""

    def __init__(self) -> None:
        self.password = PASSWORD
        self.generation = 1  # raise it to expire the tokens given so far
        self.fail: dict[str, tuple[int, Any]] = {}  # path fragment -> (status, body)
        self.exception: Exception | None = None
        self.stations = list(STATIONS)
        self.silent: set[str] = set()  # stations without records
        self.soil = {"XX01"}
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    @property
    def good_token(self) -> str:
        return f"token+{self.generation}="

    def count(self, fragment: str) -> int:
        return sum(fragment in path for path, _ in self.calls)

    async def handle(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if self.exception:
            raise self.exception
        path = url.path.split("/API/V1/")[1]
        query = list(url.query.items())
        self.calls.append((path, query))
        status, body = self._answer(path, url.query)
        for fragment, answer in self.fail.items():
            if fragment in path:
                status, body = answer
        key = "text" if isinstance(body, str) else "json"
        return AiohttpClientMockResponse(method=method, url=url, status=status, **{key: body})

    def _answer(self, path: str, query: Any) -> tuple[int, Any]:
        if path == "Autenticacion/cifrarCadena":
            return 200, encrypted(query["cadena"])
        if path == "Autenticacion/obtenerToken":
            right = query["Usuario"] == encrypted(USER) and query["Password"] == encrypted(self.password)
            return 200, self.good_token if right else "token+falso="
        if query.get("token") != self.good_token:
            return 500, ""
        if path == "Info/ACCESOS":
            return 200, {"datos": [ACCESSES], "MensajeRespuesta": None}
        if path == "Info/ESTACIONES":
            return 200, {"datos": self.stations, "MensajeRespuesta": None}
        ids = [code for code in query.getall("Id") if code not in self.silent]
        start, end = date.fromisoformat(query["FechaInicial"]), date.fromisoformat(query["FechaFinal"])
        make = self._hourly if "Horarios" in path else self._daily
        return 200, {"datos": [row for code in ids for row in make(code, start, end)], "MensajeRespuesta": None}

    def _hourly(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        rows = []
        when = datetime(start.year, start.month, start.day, 0, 30, tzinfo=dt_util.UTC)
        while when <= dt_util.utcnow() and (when - timedelta(minutes=30)).date() <= end:
            day = (when - timedelta(minutes=30)).date()  # midnight is "2400" of the previous day
            hormin = 2400 if (when.hour, when.minute) == (0, 0) else when.hour * 100 + when.minute
            rows.append(
                {
                    "Fecha": f"{day.isoformat()}T00:00:00",
                    "Estacion": code,
                    "HorMin": hormin,
                    "TempMedia": 18.4,
                    "HumedadMedia": 55,
                    "VelViento": 2.5,
                    "DirViento": 270,
                    "Radiacion": 800,
                    "Precipitacion": 0.2 if when >= datetime(2026, 5, 9, 22, 0, tzinfo=dt_util.UTC) else 0.0,
                    "TempSuelo1": 15.2 if code in self.soil else -999,
                    "TempSuelo2": -999,
                }
            )
            when += timedelta(minutes=30)
        # the newest record is dry, to give the weather entity a clear sky
        if rows:
            rows[-1]["Precipitacion"] = 0.0
        return rows

    def _daily(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        rows = []
        day = start
        while day < min(end, dt_util.utcnow().date()):
            rows.append(
                {
                    "Fecha": f"{day.isoformat()}T00:00:00",
                    "Estacion": code,
                    "TempMedia": 15.1,
                    "TempMax": 24.3,
                    "TempMin": 6.2,
                    "HumedadMedia": 61,
                    "HumedadMax": 90,
                    "humedadMin": 30,
                    "VelViento": 2.1,
                    "VelVientoMax": 9.4,
                    "DirViento": 200,
                    "Radiacion": 24.5,
                    "Precipitacion": 1.4,
                    "EtPMon": 4.12,
                    "PePMon": 0.9,
                }
            )
            day += timedelta(days=1)
        return rows


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
async def home(hass, freezer):
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Madrid")
    await hass.config.async_update(latitude=40.0, longitude=-4.0)


@pytest.fixture
def siar(aioclient_mock) -> FakeSiar:
    fake = FakeSiar()
    aioclient_mock.get(re.compile(r"/siarapi/API/V1/"), side_effect=fake.handle)
    return fake


def make_entry(codes: tuple[str, ...] = ("XX01",), password: str = PASSWORD) -> MockConfigEntry:
    stations = {
        code: {"name": f"Estación {code}", "town": "Villa Ejemplo", "latitude": 40.1, "longitude": -4.0, "network": "SiAR"}
        for code in codes
    }
    return MockConfigEntry(
        domain=DOMAIN,
        title="SiAR",
        unique_id=account_id(USER),
        data={CONF_USERNAME: USER, CONF_PASSWORD: password},
        options={CONF_STATIONS: stations, CONF_INTERVAL: 60},
    )
