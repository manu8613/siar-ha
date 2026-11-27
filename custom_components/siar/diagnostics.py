"""Diagnostics without credentials."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import SiarConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SiarConfigEntry) -> dict[str, Any]:
    """What is needed to look into a problem."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "accesses": coordinator.data.accesses,
        "stations": {code: asdict(data) for code, data in coordinator.data.stations.items()},
    }
