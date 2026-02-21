"""Sensor entities for Solar Surplus EV Charging."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarChargerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SolarChargerStatusSensor(coordinator, entry),
            SolarChargerCurrentSensor(coordinator, entry),
            SolarChargerPowerSensor(coordinator, entry),
        ]
    )


class _SolarChargerEntity(CoordinatorEntity):
    """Base class with a shared device info block."""

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }


class SolarChargerStatusSensor(_SolarChargerEntity, SensorEntity):
    _attr_name = "Solar Charger Status"
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str:
        return self.coordinator.data["status"]


class SolarChargerCurrentSensor(_SolarChargerEntity, SensorEntity):
    _attr_name = "Solar Charger Current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "A"

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current"

    @property
    def native_value(self) -> float:
        return self.coordinator.data["current_a"]


class SolarChargerPowerSensor(_SolarChargerEntity, SensorEntity):
    _attr_name = "Solar Charger Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.data["power_w"]
