"""Diagnostic sensors for Pacific Power integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_UTILITY, DOMAIN, UTILITY_DOMAINS, UTILITY_PACIFIC_POWER
from .coordinator import (
    PacificPowerConfigEntry,
    PacificPowerCoordinator,
    PacificPowerData,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PacificPowerSensorDescription(SensorEntityDescription):
    """Describe a Pacific Power sensor."""

    value_fn: Callable[[PacificPowerData], datetime | None]


SENSOR_DESCRIPTIONS: tuple[PacificPowerSensorDescription, ...] = (
    PacificPowerSensorDescription(
        key="last_data_received",
        translation_key="last_data_received",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_data_received,
    ),
    PacificPowerSensorDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_updated,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PacificPowerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pacific Power sensors."""
    coordinator = entry.runtime_data
    data = coordinator.data
    account_key = (
        f"{data.account.customer_idn}_{data.account.account_sequence}"
    )
    utility = entry.data.get(CONF_UTILITY, UTILITY_PACIFIC_POWER)
    utility_name = UTILITY_DOMAINS[utility]["name"]

    entities: list[PacificPowerSensor] = []
    for description in SENSOR_DESCRIPTIONS:
        entities.append(
            PacificPowerSensor(
                coordinator=coordinator,
                description=description,
                account_key=account_key,
                account_data=data,
                utility_name=utility_name,
            )
        )

    async_add_entities(entities)


class PacificPowerSensor(
    CoordinatorEntity[PacificPowerCoordinator], SensorEntity
):
    """A diagnostic sensor for Pacific Power."""

    entity_description: PacificPowerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PacificPowerCoordinator,
        description: PacificPowerSensorDescription,
        account_key: str,
        account_data: PacificPowerData,
        utility_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._account_key = account_key
        self._attr_unique_id = f"{account_key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_key)},
            name=f"{utility_name} {account_data.account.address}",
            manufacturer="PacifiCorp",
            model="Energy Usage",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the sensor value."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return None
