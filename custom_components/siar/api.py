"""Client of the SiAR Web API (pure Python, no Home Assistant imports).

Source: "Manual Web API SiAR", version 2.2 (May 2025), Spanish Ministry of
Agriculture, Fisheries and Food.

1. ``Autenticacion/cifrarCadena`` encrypts the NIF and the password.
2. ``Autenticacion/obtenerToken`` turns both into a token.
3. ``Info/...`` and ``Datos/...`` take that token in the query string.

``obtenerToken`` answers with something that looks like a token even for wrong
credentials, so a login only counts once a protected request (``Info/ACCESOS``)
has worked with it.

Every secret travels in the query string (that is how the API is designed), so
no URL may ever reach a log or an error message, and redirects are not followed.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiohttp

BASE_URL = "https://servicio.mapa.gob.es/siarapi/API/V1"
TIMEOUT = 30  # s
MAX_IDS = 10  # stations per data request
INVALID = -999.0  # -999: no sensor; -6999 / -99999: datalogger failure

_LIMIT_WORDS = ("limite", "límite", "superad", "excedid", "excede", "demasiadas")


class SiarError(Exception):
    """Base error. The text never contains URLs or credentials."""


class SiarConnectionError(SiarError):
    """The service could not be reached or is down."""


class SiarAuthError(SiarError):
    """Credentials or token rejected.

    ``definitive`` is False when the service only answered HTTP 500, which is
    what it does with an invalid token but could also be a failure of its own.
    """

    def __init__(self, message: str, *, definitive: bool = True) -> None:
        super().__init__(message)
        self.definitive = definitive


class SiarRateLimitError(SiarError):
    """Request or record limit of the account exceeded."""


class SiarResponseError(SiarError):
    """The answer is not what the manual describes."""


@dataclass(frozen=True, slots=True)
class Station:
    """One station of ``Info/ESTACIONES``."""

    code: str
    name: str
    town: str = ""
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    network: str = ""
    closed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """For the config entry."""
        return {
            "name": self.name,
            "town": self.town,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "network": self.network,
        }


@dataclass(frozen=True, slots=True)
class Reading:
    """One half-hourly or daily record of a station.

    ``time`` is the UTC end of the half hour, or UTC midnight of the day.
    """

    station: str
    time: datetime
    values: Mapping[str, float | None]


# JSON field (lower case) -> key used by the integration
HOURLY_FIELDS = {
    "tempmedia": "temperature",
    "humedadmedia": "humidity",
    "velviento": "wind_speed",
    "dirviento": "wind_bearing",
    "radiacion": "irradiance",
    "precipitacion": "precipitation",
    "tempsuelo1": "soil_temperature_1",
    "tempsuelo2": "soil_temperature_2",
}
DAILY_FIELDS = {
    "tempmedia": "temperature",
    "tempmax": "temperature_max",
    "tempmin": "temperature_min",
    "humedadmedia": "humidity",
    "humedadmax": "humidity_max",
    "humedadmin": "humidity_min",
    "velviento": "wind_speed",
    "velvientomax": "wind_speed_max",
    "dirviento": "wind_bearing",
    "radiacion": "radiation",
    "precipitacion": "precipitation",
    "etpmon": "et0",
    "pepmon": "effective_precipitation",
}


# --------------------------------------------------------------------- parsing


def decode(raw: bytes, charset: str | None = None) -> str:
    """Text of an answer: declared charset, else UTF-8, else Windows-1252. Never fails."""
    for encoding in (charset, "utf-8", "cp1252"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1")


def _lower(record: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in record.items()}


def number(value: Any) -> float | None:
    """A measurement, or None for blanks, sentinels and rubbish."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(result) or result <= INVALID:
        return None
    return result


def message_of(payload: Any) -> str:
    """``MensajeRespuesta`` (or similar) of an answer, empty if there is none."""
    if isinstance(payload, Mapping):
        data = _lower(payload)
        for key in ("mensajerespuesta", "message", "mensaje"):
            if isinstance(data.get(key), str):
                return data[key].strip()[:200]
    return ""


def records(payload: Any) -> list[dict[str, Any]]:
    """The list under ``datos``, keys in lower case."""
    if not isinstance(payload, Mapping):
        raise SiarResponseError("La respuesta de SiAR no es el JSON esperado")
    data = _lower(payload).get("datos")
    if data is None:
        if text := message_of(payload):
            raise _classify(200, text)
        raise SiarResponseError("La respuesta de SiAR no trae el campo «datos»")
    if isinstance(data, Mapping):
        data = [data]
    if not isinstance(data, list):
        raise SiarResponseError("El campo «datos» de SiAR no es una lista")
    if not data and (text := message_of(payload)):
        error = _classify(200, text)
        if isinstance(error, SiarAuthError | SiarRateLimitError):
            raise error  # any other note on an empty answer just means "no records"
    return [_lower(item) for item in data if isinstance(item, Mapping)]


def parse_dms(value: Any) -> float | None:
    """``391520000N`` (degrees, MM, SS.sss, hemisphere) -> decimal degrees."""
    match = re.fullmatch(r"\s*(\d{7,10})\s*([NSEWO])\s*", str(value or "").upper())
    if not match:
        return None
    digits, hemisphere = match.groups()
    digits = digits.zfill(8)
    degrees = int(digits[:-7])
    minutes = int(digits[-7:-5])
    seconds = int(digits[-5:]) / 1000
    if minutes >= 60 or seconds >= 60:
        return None
    result = degrees + minutes / 60 + seconds / 3600
    return round(-result if hemisphere in "SWO" else result, 6)


def parse_stations(payload: Any) -> list[Station]:
    """Stations of ``Info/ESTACIONES``, sorted by code."""
    found: dict[str, Station] = {}
    for item in records(payload):
        code = str(item.get("codigo") or "").strip().upper()
        if not code:
            continue
        station = Station(
            code=code,
            name=str(item.get("estacion") or code).strip(),
            town=str(item.get("termino") or "").strip(),
            latitude=parse_dms(item.get("latitud")),
            longitude=parse_dms(item.get("longitud")),
            altitude=number(item.get("altitud")),
            network=str(item.get("red_estacion") or "").strip(),
            closed=bool(item.get("fecha_baja")),
        )
        # a code listed twice (station moved): keep the one in service
        if code not in found or found[code].closed:
            found[code] = station
    return [found[code] for code in sorted(found)]


def parse_day(value: Any) -> date | None:
    """``2026-05-01T00:00:00``, ``2026-05-01`` or ``01/05/2026``."""
    text = str(value or "").strip()
    try:
        if match := re.match(r"(\d{4})-(\d{2})-(\d{2})", text):
            return date(int(match[1]), int(match[2]), int(match[3]))
        if match := re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
            return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        return None
    return None


def parse_readings(payload: Any, *, hourly: bool) -> list[Reading]:
    """Records of ``Datos/...``, oldest first. SiAR publishes them in UTC."""
    fields = HOURLY_FIELDS if hourly else DAILY_FIELDS
    result = []
    for item in records(payload):
        station = str(item.get("estacion") or "").strip().upper()
        day = parse_day(item.get("fecha"))
        if not station or day is None:
            continue
        when = datetime(day.year, day.month, day.day, tzinfo=UTC)
        if hourly:
            hhmm = number(item.get("hormin", item.get("horamin")))
            if hhmm is None:
                continue
            hours, minutes = divmod(int(hhmm), 100)
            if not (0 <= hours <= 24 and 0 <= minutes < 60) or (hours == 24 and minutes):
                continue
            when += timedelta(hours=hours, minutes=minutes)
        values = {key: number(item.get(field)) for field, key in fields.items()}
        if all(value is None for value in values.values()):
            continue
        result.append(Reading(station, when, values))
    return sorted(result, key=lambda r: (r.station, r.time))


def _classify(status: int, text: str) -> SiarError:
    """Error for a refused request, from its status and the server message."""
    detail = f"HTTP {status}: {text}" if text else f"HTTP {status}"
    lowered = text.lower()
    if status == 429 or (status in (200, 401, 403) and any(word in lowered for word in _LIMIT_WORDS)):
        return SiarRateLimitError(detail)
    if status in (401, 403):
        return SiarAuthError(detail)
    if status == 500:
        return SiarAuthError(detail, definitive=False)
    if status > 500:
        return SiarConnectionError(detail)
    if status == 200 and any(word in lowered for word in ("token", "autoriz", "permiso", "credencial", "usuario")):
        return SiarAuthError(detail)
    return SiarResponseError(detail)


# --------------------------------------------------------------------- client


class SiarClient:
    """One SiAR account."""

    def __init__(self, session: aiohttp.ClientSession, user: str, password: str, base_url: str = BASE_URL) -> None:
        self._session = session
        self._user = user
        self._password = password
        self._base = base_url.rstrip("/")
        self._token = ""
        self._secrets: set[str] = {value for value in (user, password) if len(value) >= 3}
        self._lock = asyncio.Lock()
        self.accesses: dict[str, float | None] = {}

    def _redact(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, "***")
        return text

    async def _get(self, path: str, params: list[tuple[str, str]]) -> Any:
        """GET one service. Returns the JSON, or the plain text of the answer."""
        try:
            async with asyncio.timeout(TIMEOUT):
                async with self._session.get(f"{self._base}/{path}", params=params, allow_redirects=False) as response:
                    status = response.status
                    text = decode(await response.read(), response.charset)
        except TimeoutError:
            raise SiarConnectionError("SiAR no responde (tiempo agotado)") from None
        except (aiohttp.ClientError, OSError) as err:
            # "from None": the original exception can carry the URL, with the secrets
            raise SiarConnectionError(f"No se puede conectar con SiAR ({type(err).__name__})") from None
        try:
            payload: Any = json.loads(text)
        except ValueError:
            payload = text.strip()
        if status != 200:
            message = message_of(payload)
            if not message and isinstance(payload, str) and not payload.startswith("<"):
                message = payload[:200]
            raise _classify(status, self._redact(message))
        return payload

    async def _auth_string(self, path: str, params: list[tuple[str, str]], what: str) -> str:
        """An answer of ``Autenticacion``: a single string without blanks."""
        try:
            payload = await self._get(path, params)
        except SiarAuthError as err:
            if not err.definitive:
                # these two services need no token: a 500 here is the server failing
                raise SiarConnectionError(str(err)) from None
            raise
        if isinstance(payload, Mapping):
            data = _lower(payload)
            payload = next((data[k] for k in ("token", "cadena", "datos") if isinstance(data.get(k), str)), message_of(data))
        value = payload.strip().strip('"') if isinstance(payload, str) else ""
        if not value:
            raise SiarAuthError(f"SiAR no devuelve {what}")
        if value.startswith("<"):
            raise SiarResponseError(f"SiAR devuelve una página web en lugar de {what}")
        if any(char.isspace() for char in value):
            raise SiarAuthError(self._redact(value[:200]))
        self._secrets.add(value)
        return value

    async def login(self) -> None:
        """Get a token and prove that it works. Raises ``SiarError``."""
        async with self._lock:
            self._token = ""
            user = await self._auth_string("Autenticacion/cifrarCadena", [("cadena", self._user)], "el usuario cifrado")
            password = await self._auth_string("Autenticacion/cifrarCadena", [("cadena", self._password)], "la contraseña cifrada")
            token = await self._auth_string("Autenticacion/obtenerToken", [("Usuario", user), ("Password", password)], "el token")
            self.accesses = self._parse_accesses(await self._get("Info/ACCESOS", [("token", token)]))
            self._token = token

    @staticmethod
    def _parse_accesses(payload: Any) -> dict[str, float | None]:
        items = records(payload)
        if not items or "maxaccesosdia" not in items[0]:
            raise SiarResponseError("SiAR no devuelve los límites de la cuenta")
        return {key: number(value) for key, value in items[0].items()}

    async def _protected(self, path: str, params: list[tuple[str, str]]) -> Any:
        """A request with token; logs in again once if the token is refused."""
        if self._token:
            try:
                return await self._get(path, [("token", self._token), *params])
            except SiarAuthError:
                pass  # expired token: get another one
        await self.login()
        try:
            return await self._get(path, [("token", self._token), *params])
        except SiarAuthError as err:
            if err.definitive:
                raise
            # the token has just worked: a 500 here is the service, not the credentials
            raise SiarConnectionError(str(err)) from None

    async def refresh_accesses(self) -> dict[str, float | None]:
        """Usage and limits of the account."""
        self.accesses = self._parse_accesses(await self._protected("Info/ACCESOS", []))
        return self.accesses

    async def stations(self) -> list[Station]:
        """Stations known to the service."""
        result = parse_stations(await self._protected("Info/ESTACIONES", []))
        if not result:
            raise SiarResponseError("SiAR no devuelve ninguna estación")
        return result

    async def readings(self, codes: Iterable[str], start: date, end: date, *, hourly: bool) -> list[Reading]:
        """Half-hourly (``hourly``) or daily records of several stations."""
        wanted = sorted({code.upper() for code in codes})
        path = f"Datos/{'Horarios' if hourly else 'Diarios'}/ESTACION"
        extra = [("FechaInicial", start.isoformat()), ("FechaFinal", end.isoformat())]
        if not hourly:
            extra.append(("DatosCalculados", "true"))
        result: list[Reading] = []
        for first in range(0, len(wanted), MAX_IDS):
            ids = [("Id", code) for code in wanted[first : first + MAX_IDS]]
            result.extend(parse_readings(await self._protected(path, [*ids, *extra]), hourly=hourly))
        return [reading for reading in result if reading.station in wanted]
