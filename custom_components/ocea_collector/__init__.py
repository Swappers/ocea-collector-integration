"""The Ocea Collector integration."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .coordinator import OceaCoordinator
from .const import DOMAIN, SERVICE_FETCH

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ocea Collector from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    coordinator = OceaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    if not hass.data[DOMAIN].get("service_registered"):

        async def _handle_fetch(call) -> None:
            entry_id = call.data.get("entry_id")
            entries = hass.config_entries.async_entries(DOMAIN)
            targets: list[OceaCoordinator] = []
            if entry_id:
                target_entry = next(
                    (item for item in entries if item.entry_id == entry_id), None
                )
                if target_entry and target_entry.runtime_data:
                    targets.append(target_entry.runtime_data)
            else:
                targets = [
                    item.runtime_data
                    for item in entries
                    if getattr(item, "runtime_data", None)
                ]

            if not targets:
                _LOGGER.warning("No Ocea entries available to refresh.")
                return

            _LOGGER.warning(
                "Manual fetch service called (entries=%s).",
                [item.config_entry.entry_id for item in targets],
            )
            await asyncio.gather(
                *(target.async_request_refresh() for target in targets)
            )

        hass.services.async_register(
            DOMAIN, SERVICE_FETCH, _handle_fetch, schema=SERVICE_SCHEMA
        )
        hass.data[DOMAIN]["service_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data = None
        if not any(
            item.state == ConfigEntryState.LOADED
            for item in hass.config_entries.async_entries(DOMAIN)
        ):
            if hass.data.get(DOMAIN, {}).get("service_registered"):
                hass.services.async_remove(DOMAIN, SERVICE_FETCH)
                hass.data[DOMAIN].pop("service_registered", None)
    return unload_ok
