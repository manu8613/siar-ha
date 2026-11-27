"""Base entities: one device per station, one for the account."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_STATIONS, DOMAIN, MANUFACTURER, WEB_URL
from .coordinator import SiarCoordinator, StationData


class SiarStationEntity(CoordinatorEntity[SiarCoordinator]):
    """Entity of one station."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: SiarCoordinator, code: str) -> None:
        super().__init__(coordinator)
        self.code = code
        entry = coordinator.config_entry
        self.info: dict[str, Any] = entry.options[CONF_STATIONS][code]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{code}")},
            name=f"SiAR {self.info.get('name') or code} ({code})",
            manufacturer=MANUFACTURER,
            model=self.info.get("network") or "Estación agroclimática",
            serial_number=code,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=WEB_URL,
            via_device_id=coordinator.account_device_id,
        )

    @property
    def station(self) -> StationData:
        return self.coordinator.data.stations.get(self.code) or StationData()


def account_device(coordinator: SiarCoordinator) -> DeviceInfo:
    """Device of the account."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
        name="SiAR cuenta",
        manufacturer=MANUFACTURER,
        model="API SiAR",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=WEB_URL,
    )
