"""Download of the selected stations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Reading, SiarAuthError, SiarClient, SiarError
from .const import (
    AUTH_FAILURES_BEFORE_REAUTH,
    CONF_INTERVAL,
    CONF_STATIONS,
    DAILY_DAYS_BACK,
    DAILY_REFRESH,
    DEFAULT_INTERVAL,
    DOMAIN,
    HOURLY_DAYS_BACK,
    STALE_AFTER,
)

_LOGGER = logging.getLogger(__name__)

type SiarConfigEntry = ConfigEntry[SiarCoordinator]


@dataclass(slots=True)
class StationData:
    """What is known about one station."""

    latest: Reading | None = None  # newest half hour, None if older than STALE_AFTER
    last_time: datetime | None = None  # time of the newest half hour, however old
    rain_today: float | None = None  # mm since ``rain_since``, None without records of today
    rain_since: datetime | None = None  # local midnight that starts the sum
    day: Reading | None = None  # newest complete day


@dataclass(slots=True)
class SiarData:
    """Result of one refresh."""

    stations: dict[str, StationData] = field(default_factory=dict)
    accesses: dict[str, float | None] = field(default_factory=dict)


def summarize(codes: list[str], hourly: list[Reading], daily: list[Reading], now: datetime) -> dict[str, StationData]:
    """Newest half hour, today's rain and newest complete day of each station."""
    local_midnight = dt_util.start_of_local_day(dt_util.as_local(now))
    result: dict[str, StationData] = {}
    for code in codes:
        data = StationData()
        half_hours = sorted((r for r in hourly if r.station == code and r.time <= now + timedelta(minutes=30)), key=lambda r: r.time)
        if half_hours:
            data.last_time = half_hours[-1].time
            if now - data.last_time <= STALE_AFTER:
                data.latest = half_hours[-1]
        # a record covers the half hour that ends at its time: midnight belongs to yesterday
        rain = [
            r.values["precipitation"]
            for r in {r.time: r for r in half_hours}.values()
            if r.time > local_midnight and r.values.get("precipitation") is not None
        ]
        if rain:
            data.rain_today = round(sum(rain), 2)
            data.rain_since = local_midnight
        days = sorted((r for r in daily if r.station == code and r.time.date() < now.date()), key=lambda r: r.time)
        if days:
            data.day = days[-1]
        result[code] = data
    return result


class SiarCoordinator(DataUpdateCoordinator[SiarData]):
    """One account, several stations, as few requests as possible."""

    config_entry: SiarConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SiarConfigEntry, client: SiarClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
        )
        self.client = client
        self.account_device_id = ""  # filled in before the platforms are set up
        self.codes: list[str] = sorted(entry.options.get(CONF_STATIONS, {}))
        self._daily: list[Reading] = []
        self._daily_at: datetime | None = None
        self._auth_failures = 0

    async def _async_update_data(self) -> SiarData:
        now = dt_util.utcnow()
        today = now.date()
        try:
            hourly = await self.client.readings(self.codes, today - timedelta(days=HOURLY_DAYS_BACK), today, hourly=True)
            if self._daily_at is None or now - self._daily_at >= DAILY_REFRESH or self._daily_at.date() != today:
                self._daily = await self.client.readings(self.codes, today - timedelta(days=DAILY_DAYS_BACK), today, hourly=False)
                self._daily_at = now
        except SiarAuthError as err:
            self._auth_failures += 1
            if err.definitive or self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(f"SiAR rechaza las credenciales: {err}") from None
            raise UpdateFailed(f"SiAR rechaza la petición: {err}") from None
        except SiarError as err:
            raise UpdateFailed(str(err)) from None
        self._auth_failures = 0

        try:
            accesses = await self.client.refresh_accesses()
        except SiarError as err:  # only informative: never lose the weather data over it
            _LOGGER.debug("No se pueden leer los límites de la cuenta: %s", err)
            accesses = {}
        return SiarData(summarize(self.codes, hourly, self._daily, now), accesses)
