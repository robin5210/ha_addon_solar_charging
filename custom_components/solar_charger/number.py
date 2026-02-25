"""Number entities for Solar Surplus EV Charging runtime power settings."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_CHARGE_NOW_POWER_W,
    DEFAULT_MAX_GRID_POWER_W,
    DEFAULT_MAX_GRID_PRICE,
    DEFAULT_MIN_CHARGE_HOURS,
    DEFAULT_PEAK_POWER_LIMIT_W,
    DEFAULT_TARGET_SOC_PCT,
    DOMAIN,
)
from .coordinator import SolarChargerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarChargerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        MaxGridPowerNumber(coordinator, entry),
        ChargeNowPowerNumber(coordinator, entry),
        TargetSocNumber(coordinator, entry),
        MaxGridPriceNumber(coordinator, entry),
        MinChargeHoursNumber(coordinator, entry),
        PeakPowerLimitNumber(coordinator, entry),
    ])


class _BasePowerNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Base class for runtime power number entities."""

    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 100
    _attr_native_max_value = 11000
    _attr_native_step = 100
    _attr_mode = NumberMode.BOX

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


class MaxGridPowerNumber(_BasePowerNumber):
    """Adjustable grid assist power for solar-assisted charging mode."""

    _attr_name = "Max Grid Power"
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_max_grid_power"

    async def async_added_to_hass(self) -> None:
        """Restore value across HA restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unavailable", "unknown", ""):
            try:
                value = int(float(last.state))
                await self.coordinator.async_set_max_grid_power(value)
            except ValueError:
                pass

    @property
    def native_value(self) -> int:
        return self.coordinator.data.get("max_grid_power_w", DEFAULT_MAX_GRID_POWER_W)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_max_grid_power(int(value))


class ChargeNowPowerNumber(_BasePowerNumber):
    """Adjustable target power for charge-now and cheap-grid modes."""

    _attr_name = "Charge Now Power"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_now_power"

    async def async_added_to_hass(self) -> None:
        """Restore value across HA restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unavailable", "unknown", ""):
            try:
                value = int(float(last.state))
                await self.coordinator.async_set_charge_now_power(value)
            except ValueError:
                pass

    @property
    def native_value(self) -> int:
        return self.coordinator.data.get("charge_now_power_w", DEFAULT_CHARGE_NOW_POWER_W)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_charge_now_power(int(value))


class TargetSocNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Target SoC for cheap-grid charging. Grid charging stops when this is reached."""

    _attr_name = "Target SoC"
    _attr_icon = "mdi:battery-charging-80"
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_min_value = 20
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_target_soc"

    async def async_added_to_hass(self) -> None:
        """Restore value across HA restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unavailable", "unknown", ""):
            try:
                value = float(last.state)
                await self.coordinator.async_set_target_soc(value)
            except ValueError:
                pass

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }

    @property
    def native_value(self) -> float:
        return self.coordinator.data.get("target_soc_pct", DEFAULT_TARGET_SOC_PCT)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_target_soc(value)


class MaxGridPriceNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Maximum electricity price for cheap-grid charging."""

    _attr_name = "Max Grid Price"
    _attr_icon = "mdi:currency-eur"
    _attr_native_min_value = 0.00
    _attr_native_max_value = 1.00
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_grid_price"

    async def async_added_to_hass(self) -> None:
        """Restore value across HA restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unavailable", "unknown", ""):
            try:
                value = float(last.state)
                await self.coordinator.async_set_max_grid_price(value)
            except ValueError:
                pass

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }

    @property
    def native_value(self) -> float:
        return self.coordinator.data.get("max_grid_price", DEFAULT_MAX_GRID_PRICE)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_max_grid_price(value)


class MinChargeHoursNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Minimum charging hours per day used when SoC / battery data is unavailable."""

    _attr_name = "Min Charge Hours"
    _attr_icon = "mdi:clock-time-eight"
    _attr_native_unit_of_measurement = "h"
    _attr_native_min_value = 1
    _attr_native_max_value = 12
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_min_charge_hours"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unavailable", "unknown", ""):
            try:
                await self.coordinator.async_set_min_charge_hours(int(float(last.state)))
            except ValueError:
                pass

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }

    @property
    def native_value(self) -> int:
        return self.coordinator.data.get("min_charge_hours", DEFAULT_MIN_CHARGE_HOURS)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_min_charge_hours(int(value))


class PeakPowerLimitNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Capacity tariff peak power limit. Charging is throttled or paused when
    total grid import would exceed this value. Set to 0 to disable."""

    _attr_name = "Peak Power Limit"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0
    _attr_native_max_value = 25000
    _attr_native_step = 100
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SolarChargerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_peak_power_limit"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unavailable", "unknown", ""):
            try:
                await self.coordinator.async_set_peak_power_limit(int(float(last.state)))
            except ValueError:
                pass

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Solar Charger",
            "manufacturer": "Daheim Laden",
            "model": "Daheim Lader",
        }

    @property
    def native_value(self) -> int:
        return self.coordinator.data.get("peak_power_limit_w", DEFAULT_PEAK_POWER_LIMIT_W)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_peak_power_limit(int(value))
