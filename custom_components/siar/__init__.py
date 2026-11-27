"""SiAR: agroclimatic stations of the Spanish irrigation network in Home Assistant."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SiarClient
from .const import CONF_STATIONS, DOMAIN
from .coordinator import SiarConfigEntry, SiarCoordinator
from .entity import account_device

PLATFORMS = [Platform.SENSOR, Platform.WEATHER]


def _wanted_devices(entry: SiarConfigEntry) -> set[tuple[str, str]]:
    base = entry.entry_id
    return {(DOMAIN, base)} | {(DOMAIN, f"{base}_{code}") for code in entry.options.get(CONF_STATIONS, {})}


async def async_setup_entry(hass: HomeAssistant, entry: SiarConfigEntry) -> bool:
    """Set up one account."""
    client = SiarClient(async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = SiarCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    registry = dr.async_get(hass)
    # the station devices hang from the account device: it has to exist first
    account = registry.async_get_or_create(config_entry_id=entry.entry_id, **account_device(coordinator))
    coordinator.account_device_id = account.id
    # stations removed in the options lose their device and entities
    wanted = _wanted_devices(entry)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SiarConfigEntry) -> bool:
    """Unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: SiarConfigEntry, device: dr.DeviceEntry) -> bool:
    """A device can be deleted by hand only if its station is no longer selected."""
    return not device.identifiers & _wanted_devices(entry)
