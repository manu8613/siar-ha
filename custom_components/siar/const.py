"""Constants of the SiAR integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "siar"

CONF_STATIONS: Final = "stations"
CONF_INTERVAL: Final = "update_interval"

DEFAULT_INTERVAL: Final = 60  # minutes
MIN_INTERVAL: Final = 30  # SiAR records are half-hourly
MAX_INTERVAL: Final = 360
DAILY_REFRESH: Final = timedelta(hours=3)  # daily records change once a day
HOURLY_DAYS_BACK: Final = 2
DAILY_DAYS_BACK: Final = 8
STALE_AFTER: Final = timedelta(hours=48)  # older half-hourly records are not "current"
AUTH_FAILURES_BEFORE_REAUTH: Final = 3  # ambiguous refusals (HTTP 500)

ATTRIBUTION: Final = "Datos: SiAR · Ministerio de Agricultura, Pesca y Alimentación"
MANUFACTURER: Final = "Red SiAR"
WEB_URL: Final = "https://servicio.mapa.gob.es/siarweb/"
