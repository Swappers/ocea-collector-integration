"""Sensor platform for Ocea Collector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import OceaCoordinator, OceaData
from .ocea_client import FLUIDS


@dataclass(frozen=True, kw_only=True)
class OceaSensorEntityDescription(SensorEntityDescription):
    """Describe Ocea sensor entity."""

    value_fn: Callable[[OceaData, str], float | None]
    attr_fn: Callable[[OceaData, str], dict[str, Any] | None] = lambda *_: None


SENSORS: tuple[OceaSensorEntityDescription, ...] = (
    OceaSensorEntityDescription(
        key="total",
        name="Total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data, key: data.fluids[key].total,
        attr_fn=lambda data, key: {
            "last_total_at": data.fluids[key].last_total_at,
            "latest_date": data.fluids[key].latest_date,
            "api_latest_date": data.fluids[key].api_latest_date,
            "value_status": data.fluids[key].value_status,
        },
    ),
    OceaSensorEntityDescription(
        key="leak_estimate",
        name="Leak Estimate",
        state_class=None,
        value_fn=lambda data, key: data.fluids[key].leak_estimate,
        attr_fn=lambda data, key: {
            "latest_date": data.fluids[key].latest_date,
            "api_latest_date": data.fluids[key].api_latest_date,
            "value_status": data.fluids[key].value_status,
        },
    ),
    OceaSensorEntityDescription(
        key="daily",
        name="Daily",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data, key: data.fluids[key].daily,
        attr_fn=lambda data, key: {
            "latest_date": data.fluids[key].latest_date,
            "api_latest_date": data.fluids[key].api_latest_date,
            "value_status": data.fluids[key].value_status,
            "daily_status": data.fluids[key].daily_status,
            "daily_source": data.fluids[key].daily_source,
            "estimated_today": data.fluids[key].estimated_today,
            "estimated_today_source": data.fluids[key].estimated_today_source,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ocea Collector sensors."""
    coordinator: OceaCoordinator = entry.runtime_data
    entities: list[OceaSensor] = []

    for fluid_key, meta in FLUIDS.items():
        for description in SENSORS:
            if description.key == "leak_estimate" and meta.get("unit") != "m3":
                continue
            entities.append(
                OceaSensor(
                    coordinator=coordinator,
                    fluid_key=fluid_key,
                    description=description,
                )
            )

    async_add_entities(entities)


class OceaSensor(CoordinatorEntity[OceaCoordinator], SensorEntity):
    """Representation of an Ocea sensor."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: OceaSensorEntityDescription

    def __init__(
        self,
        coordinator: OceaCoordinator,
        fluid_key: str,
        description: OceaSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._fluid_key = fluid_key
        self.entity_description = description
        label = FLUIDS[fluid_key].get("label", fluid_key.replace("_", " ").title())
        self._attr_name = f"{label} {description.name}"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{fluid_key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            manufacturer="Ocea",
            name="Ocea Collector",
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.entity_description.value_fn(self.coordinator.data, self._fluid_key)
            is not None
        )

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data, self._fluid_key)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.key == "leak_estimate":
            return None
        unit = FLUIDS[self._fluid_key].get("unit")
        if unit == "L":
            return UnitOfVolume.LITERS
        if unit == "m3":
            return UnitOfVolume.CUBIC_METERS
        if unit == "kWh":
            return UnitOfEnergy.KILO_WATT_HOUR
        return unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self.entity_description.key != "total":
            return None
        unit = FLUIDS[self._fluid_key].get("unit")
        if unit == "m3":
            return SensorDeviceClass.WATER
        if unit == "kWh":
            return SensorDeviceClass.ENERGY
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attr_fn(self.coordinator.data, self._fluid_key)
