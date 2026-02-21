"""DataUpdateCoordinator for Solar Surplus EV Charging.

Owns the Modbus connection and the charging controller FSM.
`_async_update_data` is called by HA on each update_interval tick and:
  1. Reads the solar export sensor value directly from the HA state machine.
  2. Reads the enabled switch state.
  3. Runs one controller iteration (may write to the charger via Modbus).
  4. Returns a data dict consumed by sensor/switch entities.
"""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .charger import DaheimCharger
from .const import (
    CHARGING_MODE_SOLAR_ONLY,
    CONF_CHARGE_NOW_POWER_W,
    CONF_CHARGER_HOST,
    CONF_CHARGER_PORT,
    CONF_CHARGER_SLAVE_ID,
    CONF_HYSTERESIS_W,
    CONF_MAX_CURRENT,
    CONF_MAX_GRID_POWER_W,
    CONF_MIN_CURRENT,
    CONF_MIN_POWER_1PHASE,
    CONF_MIN_POWER_3PHASE,
    CONF_PHASE_1_VALUE,
    CONF_PHASE_3_VALUE,
    CONF_PHASE_SWITCH_PAUSE,
    CONF_PHASE_SWITCH_REGISTER,
    CONF_SOLAR_EXPORT_SENSOR,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    DEFAULT_CHARGE_NOW_POWER_W,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MAX_GRID_POWER_W,
    DEFAULT_MIN_CURRENT,
    DEFAULT_MIN_POWER_1PHASE,
    DEFAULT_MIN_POWER_3PHASE,
    DEFAULT_PHASE_1_VALUE,
    DEFAULT_PHASE_3_VALUE,
    DEFAULT_PHASE_SWITCH_PAUSE,
    DEFAULT_PHASE_SWITCH_REGISTER,
    DEFAULT_PORT,
    DEFAULT_SLAVE_ID,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
)
from .controller import Controller, ControllerConfig

log = logging.getLogger(__name__)


class SolarChargerCoordinator(DataUpdateCoordinator):
    """Coordinates Modbus I/O and exposes charging state to HA entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}

        super().__init__(
            hass,
            log,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
            ),
        )

        self._entry = entry
        self._solar_sensor: str = data[CONF_SOLAR_EXPORT_SENSOR]
        self._voltage: int = int(data.get(CONF_VOLTAGE, DEFAULT_VOLTAGE))
        self._max_grid_power_w: int = int(data.get(CONF_MAX_GRID_POWER_W, DEFAULT_MAX_GRID_POWER_W))
        self._charge_now_power_w: int = int(data.get(CONF_CHARGE_NOW_POWER_W, DEFAULT_CHARGE_NOW_POWER_W))
        self._mode: str = entry.options.get("charging_mode", CHARGING_MODE_SOLAR_ONLY)

        self._charger = DaheimCharger(
            host=data[CONF_CHARGER_HOST],
            port=int(data.get(CONF_CHARGER_PORT, DEFAULT_PORT)),
            slave_id=int(data.get(CONF_CHARGER_SLAVE_ID, DEFAULT_SLAVE_ID)),
            phase_switch_register=int(data.get(CONF_PHASE_SWITCH_REGISTER, DEFAULT_PHASE_SWITCH_REGISTER)),
            phase_1_value=int(data.get(CONF_PHASE_1_VALUE, DEFAULT_PHASE_1_VALUE)),
            phase_3_value=int(data.get(CONF_PHASE_3_VALUE, DEFAULT_PHASE_3_VALUE)),
        )

        cfg = ControllerConfig(
            min_current=int(data.get(CONF_MIN_CURRENT, DEFAULT_MIN_CURRENT)),
            max_current=int(data.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)),
            voltage=int(data.get(CONF_VOLTAGE, DEFAULT_VOLTAGE)),
            min_power_1phase=int(data.get(CONF_MIN_POWER_1PHASE, DEFAULT_MIN_POWER_1PHASE)),
            min_power_3phase=int(data.get(CONF_MIN_POWER_3PHASE, DEFAULT_MIN_POWER_3PHASE)),
            hysteresis_w=int(data.get(CONF_HYSTERESIS_W, DEFAULT_HYSTERESIS_W)),
            phase_switch_pause=int(data.get(CONF_PHASE_SWITCH_PAUSE, DEFAULT_PHASE_SWITCH_PAUSE)),
        )
        self._controller = Controller(self._charger, cfg)

        # Enabled flag — persisted in entry options so it survives HA restarts
        self._enabled: bool = entry.options.get("enabled", True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> bool:
        """Connect to the charger. Called once from async_setup_entry."""
        connected = await self._charger.connect()
        if connected:
            await self._charger.set_modbus_control_mode()
        else:
            log.warning(
                "Could not connect to Daheim Lader at %s — will retry on each update",
                self._charger.host,
            )
        return True  # Non-fatal; the update loop will keep retrying

    async def async_shutdown(self) -> None:
        """Stop charging and disconnect cleanly. Called from async_unload_entry."""
        await self._charger.stop_charging()
        await self._charger.disconnect()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator interface
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        """Fetch solar state, run one control iteration, return entity data."""
        solar_w = self._read_solar_export()
        enabled = self._enabled

        log.debug(
            "Update tick — mode: %s, solar: %s W, enabled: %s, controller state: %s",
            self._mode,
            f"{solar_w:.0f}" if solar_w is not None else "unavailable",
            enabled,
            self._controller.status,
        )

        if self._mode == "charge_now":
            effective_solar: float | None = float(self._charge_now_power_w)
            force_charging = True
        elif self._mode == "solar_assisted":
            effective_solar = (solar_w or 0.0) + self._max_grid_power_w
            force_charging = True
        else:  # solar_only
            effective_solar = solar_w
            force_charging = False

        try:
            await self._controller.async_update(effective_solar, enabled, force_charging)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in charging controller: %s", exc)
            raise UpdateFailed(f"Controller error: {exc}") from exc

        # Read actual phase currents for accurate sensor reporting.
        # Falls back to controller-commanded values if the charger is unreachable.
        currents = await self._charger.get_phase_currents()
        if currents is not None:
            i1, i2, i3 = currents
            current_a = max(i1, i2, i3)  # per-phase current (all active phases are equal)
            power_w = (i1 + i2 + i3) * self._voltage
            log.debug(
                "Phase currents — L1: %.1f A, L2: %.1f A, L3: %.1f A → %.0f W",
                i1, i2, i3, power_w,
            )
        else:
            current_a = self._controller.current_amps
            power_w = self._controller.power_watts
            log.debug(
                "Phase current read failed — using commanded values: %.1f A, %.0f W",
                current_a, power_w,
            )

        log.debug(
            "Tick result — status: %s, current: %.1f A, power: %.0f W",
            self._controller.status, current_a, power_w,
        )
        return {
            "status": self._controller.status,
            "current_a": current_a,
            "power_w": power_w,
            "enabled": self._enabled,
            "charging_mode": self._mode,
        }

    # ------------------------------------------------------------------
    # Enable / disable (called by the switch entity)
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def async_set_enabled(self, value: bool) -> None:
        """Toggle charging control and persist the state in config entry options."""
        self._enabled = value
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, "enabled": value},
        )
        await self.async_refresh()

    async def async_set_mode(self, mode: str) -> None:
        """Switch charging mode and persist in config entry options."""
        log.info("Charging mode changed: %s → %s", self._mode, mode)
        self._mode = mode
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, "charging_mode": mode},
        )
        await self.async_refresh()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_solar_export(self) -> float | None:
        """Read solar export from the HA state machine. Returns W or None."""
        state = self.hass.states.get(self._solar_sensor)
        if state is None:
            log.warning("Solar sensor '%s' not found in HA state machine", self._solar_sensor)
            return None
        if state.state in ("unavailable", "unknown", ""):
            log.warning("Solar sensor '%s' is %s", self._solar_sensor, state.state)
            return None
        try:
            value = float(state.state)
            log.debug("Solar sensor '%s' = %.1f W", self._solar_sensor, value)
            return value
        except ValueError:
            log.error(
                "Solar sensor '%s' has non-numeric state: %s",
                self._solar_sensor, state.state,
            )
            return None
