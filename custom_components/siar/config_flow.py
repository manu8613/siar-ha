"""Set-up in the interface: verified credentials, then one or several stations."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .api import SiarAuthError, SiarClient, SiarConnectionError, SiarError, SiarRateLimitError, Station
from .const import (
    CONF_INTERVAL,
    CONF_STATIONS,
    DEFAULT_INTERVAL,
    DOMAIN,
    HOURLY_DAYS_BACK,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from .coordinator import SiarConfigEntry

_LOGGER = logging.getLogger(__name__)


def account_id(user: str) -> str:
    """Unique id of an account without storing the NIF in the registries."""
    return hashlib.sha256(f"{DOMAIN}:{user.strip().upper()}".encode()).hexdigest()[:24]


def _error_key(err: SiarError) -> str:
    if isinstance(err, SiarAuthError):
        return "invalid_auth"
    if isinstance(err, SiarRateLimitError):
        return "rate_limit"
    if isinstance(err, SiarConnectionError):
        return "cannot_connect"
    return "unexpected_response"


def _credentials_schema(user: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=user): TextSelector(TextSelectorConfig(autocomplete="username")),
            vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")),
        }
    )


def _distance_km(hass: HomeAssistant, station: Station) -> float | None:
    if station.latitude is None or station.longitude is None:
        return None
    metres = distance(hass.config.latitude, hass.config.longitude, station.latitude, station.longitude)
    return None if metres is None else metres / 1000


def station_choices(hass: HomeAssistant, stations: list[Station], keep: Mapping[str, Any]) -> list[SelectOptionDict]:
    """Stations in service (plus the ones already chosen), nearest first."""
    listed = [s for s in stations if not s.closed or s.code in keep]
    far = float("inf")
    options = []
    for station in sorted(listed, key=lambda s: (d if (d := _distance_km(hass, s)) is not None else far, s.code)):
        label = f"{station.code} · {station.name}"
        if station.town and station.town.casefold() != station.name.casefold():
            label += f" ({station.town})"
        if (km := _distance_km(hass, station)) is not None:
            label += f" — {km:.0f} km"
        if station.closed:
            label += " — dada de baja"
        options.append(SelectOptionDict(value=station.code, label=label))
    # chosen before but no longer listed by SiAR: keep it selectable so it can be removed
    known = {s.code for s in listed}
    options.extend(
        SelectOptionDict(value=code, label=f"{code} · {info.get('name', code)} — ya no figura en SiAR")
        for code, info in keep.items()
        if code not in known
    )
    return options


def _stations_field(options: list[SelectOptionDict]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.DROPDOWN))


async def stations_without_data(client: SiarClient, codes: list[str]) -> list[str]:
    """Codes with no recent half-hourly record. Raises ``SiarError``."""
    today = dt_util.utcnow().date()
    readings = await client.readings(codes, today - timedelta(days=HOURLY_DAYS_BACK), today, hourly=True)
    return sorted(set(codes) - {r.station for r in readings})


class _StationStep:
    """Station selection shared by the set-up and the options."""

    hass: HomeAssistant
    _client: SiarClient
    _stations: list[Station]
    _warned: tuple[str, ...] | None = None

    async def _check_selection(self, codes: list[str], errors: dict[str, str], placeholders: dict[str, str]) -> dict[str, Any] | None:
        """Metadata of the chosen stations, or None with ``errors`` filled in.

        Stations without recent records are reported once; sending the same
        selection again accepts them.
        """
        if not codes:
            errors[CONF_STATIONS] = "no_stations"
            return None
        try:
            missing = await stations_without_data(self._client, codes)
        except SiarError as err:
            errors["base"] = _error_key(err)
            placeholders["detail"] = str(err)
            return None
        if missing and self._warned != tuple(sorted(codes)):
            self._warned = tuple(sorted(codes))
            errors[CONF_STATIONS] = "station_without_data"
            placeholders["detail"] = ", ".join(missing)
            return None
        known = {s.code: s for s in self._stations}
        previous = getattr(self, "_previous", {})
        return {code: known[code].as_dict() if code in known else previous[code] for code in sorted(codes)}


class SiarConfigFlow(_StationStep, ConfigFlow, domain=DOMAIN):
    """Add, re-authenticate or reconfigure an account."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SiarConfigEntry) -> SiarOptionsFlow:
        return SiarOptionsFlow()

    async def _verify(self, user_input: Mapping[str, str]) -> None:
        """Log in and read the station list with these credentials. Raises ``SiarError``."""
        user = user_input[CONF_USERNAME].strip().upper()
        password = user_input[CONF_PASSWORD]
        client = SiarClient(async_get_clientsession(self.hass), user, password)
        await client.login()
        self._stations = await client.stations()
        self._client = client
        self._credentials = {CONF_USERNAME: user, CONF_PASSWORD: password}

    async def _credentials_step(self, step_id: str, user_input: dict[str, str] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"detail": "", "web": "https://servicio.mapa.gob.es/siarweb/preLogin"}
        entry = None
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
        elif self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                await self._verify(user_input)
            except SiarError as err:
                errors["base"] = _error_key(err)
                placeholders["detail"] = str(err)
            except Exception:
                _LOGGER.exception("Error inesperado al comprobar las credenciales de SiAR")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(account_id(self._credentials[CONF_USERNAME]))
                if entry is None:
                    self._abort_if_unique_id_configured()
                    return await self.async_step_stations()
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates=self._credentials)

        user = (user_input or {}).get(CONF_USERNAME) or (entry.data[CONF_USERNAME] if entry else "")
        return self.async_show_form(
            step_id=step_id,
            data_schema=_credentials_schema(user),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_user(self, user_input: dict[str, str] | None = None) -> ConfigFlowResult:
        return await self._credentials_step("user", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, str] | None = None) -> ConfigFlowResult:
        return await self._credentials_step("reauth_confirm", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, str] | None = None) -> ConfigFlowResult:
        return await self._credentials_step("reconfigure", user_input)

    async def async_step_stations(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"detail": ""}
        choices = station_choices(self.hass, self._stations, {})
        selected = [choices[0]["value"]] if choices else []
        if user_input is not None:
            selected = user_input.get(CONF_STATIONS, [])
            if chosen := await self._check_selection(selected, errors, placeholders):
                return self.async_create_entry(
                    title="SiAR",
                    data=self._credentials,
                    options={CONF_STATIONS: chosen, CONF_INTERVAL: DEFAULT_INTERVAL},
                )
        placeholders["count"] = str(len(choices))
        return self.async_show_form(
            step_id="stations",
            data_schema=vol.Schema({vol.Required(CONF_STATIONS, default=selected): _stations_field(choices)}),
            errors=errors,
            description_placeholders=placeholders,
        )


class SiarOptionsFlow(_StationStep, OptionsFlowWithReload):
    """Change the stations and how often they are read."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        self._previous: dict[str, Any] = dict(entry.options.get(CONF_STATIONS, {}))
        errors: dict[str, str] = {}
        placeholders = {"detail": ""}

        if not hasattr(self, "_stations"):
            client = SiarClient(async_get_clientsession(self.hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
            try:
                self._stations = await client.stations()
            except SiarError as err:
                return self.async_abort(reason=_error_key(err), description_placeholders={"detail": str(err)})
            self._client = client

        selected = list(self._previous)
        interval = entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)
        if user_input is not None:
            selected = user_input.get(CONF_STATIONS, [])
            interval = int(user_input[CONF_INTERVAL])
            if chosen := await self._check_selection(selected, errors, placeholders):
                return self.async_create_entry(data={CONF_STATIONS: chosen, CONF_INTERVAL: interval})

        schema = vol.Schema(
            {
                vol.Required(CONF_STATIONS, default=selected): _stations_field(station_choices(self.hass, self._stations, self._previous)),
                vol.Required(CONF_INTERVAL, default=interval): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_INTERVAL,
                        max=MAX_INTERVAL,
                        step=5,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors, description_placeholders=placeholders)
