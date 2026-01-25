"""Button platform for Ocea Collector."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OceaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ocea Collector button entities."""
    coordinator: OceaCoordinator = entry.runtime_data
    async_add_entities([OceaFetchButton(coordinator)])


class OceaFetchButton(CoordinatorEntity[OceaCoordinator], ButtonEntity):
    """Button to trigger a manual refresh."""

    _attr_has_entity_name = True
    _attr_name = "Fetch now"

    def __init__(self, coordinator: OceaCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_fetch_now"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Ocea",
            name="Ocea Collector",
        )

    async def async_press(self) -> None:
        _LOGGER.warning("Manual fetch button pressed.")
        await self.coordinator.async_request_refresh()
