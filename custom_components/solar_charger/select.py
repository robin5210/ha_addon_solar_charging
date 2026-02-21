"""Select entity for Solar Surplus EV Charging mode."""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CHARGING_MODE_CHARGE_NOW,
    CHARGING_MODE_SOLAR_ASSISTED,
    CHARGING_MODE_SOLAR_ONLY,
    DOMAIN,
)
from .coordinator import SolarChargerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SolarChargerModeSelect(coordinator, entry)])


class SolarChargerModeSelect(CoordinatorEntity, RestoreEntity, SelectEntity):
    """Select entity to switch between charging modes."""

    _attr_name = "Solar Charger Mode"
    _attr_icon = "mdi:solar-power-variant"
    _attr_options = [
        CHARGING_MODE_SOLAR_ONLY,
        CHARGING_MODE_SOLAR_ASSISTED,
        CHARGING_MODE_CHARGE_NOW,
    ]

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_mode"

    async def async_added_to_hass(self) -> None:
        """Restore charging mode across HA restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            await self.coordinator.async_set_mode(last.state)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }

    @property
    def current_option(self) -> str:
        return self.coordinator.data.get("charging_mode", CHARGING_MODE_SOLAR_ONLY)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option)
