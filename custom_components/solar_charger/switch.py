"""Switch entity for Solar Surplus EV Charging."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarChargerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SolarChargerSwitch(coordinator, entry)])


class SolarChargerSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to enable or disable automatic solar surplus charging."""

    _attr_name = "Solar Charging"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_enabled"

    async def async_added_to_hass(self) -> None:
        """Restore enabled state across HA restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            restored = last.state == "on"
            await self.coordinator.async_set_enabled(restored)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["enabled"]

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003
        await self.coordinator.async_set_enabled(False)
