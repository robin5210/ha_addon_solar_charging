"""DataUpdateCoordinator for Solar Surplus EV Charging.

Owns the Modbus connection and the charging controller FSM.
`_async_update_data` is called by HA on each update_interval tick and:
  1. Reads solar export, car SoC, electricity price, and grid import sensors.
  2. Computes the charging schedule for cheap-grid mode from the price forecast.
  3. Builds a ChargeInput snapshot and passes it to the controller.
  4. Returns a data dict consumed by sensor/switch/select/number entities.

Cheap-grid scheduling
---------------------
The price sensor's `raw_today` / `raw_tomorrow` attributes (Nordpool format)
are parsed to find the cheapest N hours in the upcoming 24-48 h window, where
N is derived from the remaining SoC and battery capacity (or falls back to
`min_charge_hours`).  The `max_grid_price` acts as a hard safety cap — no
charging happens above that price even if the hour is otherwise "scheduled".
"""

import logging
import math
from datetime import datetime, timedelta
from datetime import timezone as _timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .charger import DaheimCharger
from .const import (
    CHARGING_MODE_SOLAR_ONLY,
    CONF_CAR_BATTERY_KWH,
    CONF_CAR_SOC_SENSOR,
    CONF_CHARGE_NOW_POWER_W,
    CONF_CHARGER_HOST,
    CONF_CHARGER_PORT,
    CONF_CHARGER_SLAVE_ID,
    CONF_GRID_IMPORT_SENSOR,
    CONF_MONTHLY_PEAK_SENSOR,
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
    CONF_PRICE_SENSOR,
    CONF_SOLAR_EXPORT_SENSOR,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE,
    DEFAULT_CAR_BATTERY_KWH,
    DEFAULT_CHARGE_NOW_POWER_W,
    DEFAULT_HYSTERESIS_W,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MAX_GRID_POWER_W,
    DEFAULT_MAX_GRID_PRICE,
    DEFAULT_MIN_CHARGE_HOURS,
    DEFAULT_MIN_CURRENT,
    DEFAULT_MIN_POWER_1PHASE,
    DEFAULT_MIN_POWER_3PHASE,
    DEFAULT_PEAK_POWER_LIMIT_W,
    DEFAULT_PHASE_1_VALUE,
    DEFAULT_PHASE_3_VALUE,
    DEFAULT_PHASE_SWITCH_PAUSE,
    DEFAULT_PHASE_SWITCH_REGISTER,
    DEFAULT_PORT,
    DEFAULT_SLAVE_ID,
    DEFAULT_TARGET_SOC_PCT,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VOLTAGE,
    DOMAIN,
)
from .controller import ChargeInput, Controller, ControllerConfig

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
        # Optional sensors — empty string or absent key → not configured
        self._car_soc_sensor: str | None = data.get(CONF_CAR_SOC_SENSOR) or None
        self._price_sensor: str | None = data.get(CONF_PRICE_SENSOR) or None
        self._grid_import_sensor: str | None = data.get(CONF_GRID_IMPORT_SENSOR) or None
        self._monthly_peak_sensor: str | None = data.get(CONF_MONTHLY_PEAK_SENSOR) or None

        # Fixed car characteristics (from config flow, not runtime-adjustable)
        self._car_battery_kwh: float = float(data.get(CONF_CAR_BATTERY_KWH, DEFAULT_CAR_BATTERY_KWH))

        # Runtime-adjustable settings — managed by number entities via RestoreEntity
        self._max_grid_power_w: int = int(data.get(CONF_MAX_GRID_POWER_W, DEFAULT_MAX_GRID_POWER_W))
        self._charge_now_power_w: int = int(data.get(CONF_CHARGE_NOW_POWER_W, DEFAULT_CHARGE_NOW_POWER_W))
        self._mode: str = CHARGING_MODE_SOLAR_ONLY
        self._enabled: bool = True
        self._target_soc_pct: float = DEFAULT_TARGET_SOC_PCT
        self._max_grid_price: float = DEFAULT_MAX_GRID_PRICE
        self._min_charge_hours: int = DEFAULT_MIN_CHARGE_HOURS
        self._peak_power_limit_w: int = DEFAULT_PEAK_POWER_LIMIT_W

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
        return True

    async def async_shutdown(self) -> None:
        """Stop charging and disconnect cleanly. Called from async_unload_entry."""
        await self._charger.stop_charging()
        await self._charger.disconnect()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator interface
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        """Fetch sensor states, run one control iteration, return entity data."""
        soc = self._read_sensor_float(self._car_soc_sensor, "car SoC")
        grid_import = self._read_sensor_float(self._grid_import_sensor, "grid import")
        scheduled = self._compute_scheduled_charge(soc)

        # Belgian capaciteitstarief: once the monthly peak is recorded the fee is
        # already incurred for that level, so there's no penalty for staying at it.
        # Use the higher of the user's configured budget and the month's recorded peak.
        monthly_peak = self._read_sensor_float(self._monthly_peak_sensor, "monthly peak power")
        if self._peak_power_limit_w > 0:
            effective_peak_limit_w = max(self._peak_power_limit_w, int(monthly_peak or 0))
        else:
            effective_peak_limit_w = 0  # 0 = disabled regardless of monthly peak

        inp = ChargeInput(
            solar_w=self._read_sensor_float(self._solar_sensor, "solar export"),
            mode=self._mode,
            enabled=self._enabled,
            max_grid_power_w=self._max_grid_power_w,
            charge_now_power_w=self._charge_now_power_w,
            scheduled_charge=scheduled,
            current_soc_pct=soc,
            target_soc_pct=self._target_soc_pct,
            grid_import_w=grid_import,
            peak_power_limit_w=effective_peak_limit_w,
        )

        log.debug(
            "Update tick — mode: %s, solar: %s W, SoC: %s%%, scheduled: %s, "
            "grid_import: %s W, peak_limit: %d W (configured: %d, monthly: %s), enabled: %s",
            inp.mode,
            f"{inp.solar_w:.0f}" if inp.solar_w is not None else "unavailable",
            f"{soc:.0f}" if soc is not None else "unavailable",
            scheduled,
            f"{grid_import:.0f}" if grid_import is not None else "n/a",
            effective_peak_limit_w,
            self._peak_power_limit_w,
            f"{monthly_peak:.0f}" if monthly_peak is not None else "n/a",
            inp.enabled,
        )

        try:
            await self._controller.async_update(inp)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in charging controller: %s", exc)
            raise UpdateFailed(f"Controller error: {exc}") from exc

        if self._controller.measured_amps > 0 or self._controller.status == "idle":
            current_a = self._controller.measured_amps
            power_w = self._controller.measured_power_w
        else:
            current_a = self._controller.current_amps
            power_w = self._controller.power_watts

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
            "max_grid_power_w": self._max_grid_power_w,
            "charge_now_power_w": self._charge_now_power_w,
            "target_soc_pct": self._target_soc_pct,
            "max_grid_price": self._max_grid_price,
            "min_charge_hours": self._min_charge_hours,
            "peak_power_limit_w": self._peak_power_limit_w,
            "effective_peak_limit_w": effective_peak_limit_w,
            "current_soc_pct": soc,
            "scheduled_charge": scheduled,
        }

    # ------------------------------------------------------------------
    # Cheap-grid scheduling
    # ------------------------------------------------------------------

    def _compute_scheduled_charge(self, current_soc: float | None) -> bool:
        """Return True if the current hour is within the cheapest scheduled window.

        Algorithm:
        1. SoC gate: if car is already at target, skip.
        2. Compute how many hours of charging are needed (from SoC + battery
           capacity, or fall back to min_charge_hours).
        3. Read hourly price forecast from sensor attributes (raw_today /
           raw_tomorrow — Nordpool / ENTSO-E format).
        4. Sort upcoming hours by price; select cheapest N.
        5. Apply max_grid_price as a hard safety cap.
        6. Return True if 'now' falls inside any of the selected windows.

        Falls back to a simple threshold comparison when no forecast is available.
        """
        if not self._price_sensor:
            return False

        # SoC gate
        if current_soc is not None and current_soc >= self._target_soc_pct:
            log.debug(
                "Cheap grid: SoC %.0f%% ≥ target %.0f%% — no charging needed",
                current_soc, self._target_soc_pct,
            )
            return False

        state = self.hass.states.get(self._price_sensor)
        if state is None or state.state in ("unavailable", "unknown", ""):
            return False

        now = dt_util.now()

        # ---- Try to parse hourly forecast (Nordpool / ENTSO-E format) --------
        forecast: list[tuple[datetime, datetime, float]] = []
        for attr in ("raw_today", "raw_tomorrow"):
            for entry in state.attributes.get(attr, []):
                start = entry.get("start")
                end = entry.get("end")
                value = entry.get("value")
                if start is None or end is None or value is None:
                    continue
                if isinstance(start, str):
                    start = dt_util.parse_datetime(start)
                if isinstance(end, str):
                    end = dt_util.parse_datetime(end)
                if start is None or end is None:
                    continue
                # Only keep entries that haven't ended yet
                if end > now:
                    forecast.append((start, end, float(value)))

        if not forecast:
            # No forecast data — fall back to simple threshold comparison
            try:
                price = float(state.state)
                result = price <= self._max_grid_price
                log.debug(
                    "Cheap grid (no forecast): price %.4f, threshold %.4f → %s",
                    price, self._max_grid_price, result,
                )
                return result
            except ValueError:
                return False

        # ---- Compute hours needed -------------------------------------------
        if current_soc is not None and self._car_battery_kwh > 0:
            soc_needed = max(0.0, self._target_soc_pct - current_soc)
            energy_kwh = soc_needed / 100.0 * self._car_battery_kwh
            charge_power_kw = self._charge_now_power_w / 1000.0
            hours_needed = math.ceil(energy_kwh / charge_power_kw) if charge_power_kw > 0 else self._min_charge_hours
            log.debug(
                "Cheap grid: SoC %.0f%% → target %.0f%%, need %.2f kWh → %d hours at %.1f kW",
                current_soc, self._target_soc_pct, energy_kwh, hours_needed, charge_power_kw,
            )
        else:
            hours_needed = self._min_charge_hours
            log.debug("Cheap grid: no SoC data — using min_charge_hours = %d", hours_needed)

        if hours_needed <= 0:
            return False

        # ---- Sort by price, select cheapest N within the safety cap ----------
        forecast.sort(key=lambda x: x[2])
        scheduled_windows = [w for w in forecast[:hours_needed] if w[2] <= self._max_grid_price]

        if not scheduled_windows:
            log.debug(
                "Cheap grid: cheapest %d hours all exceed max price %.4f — not charging",
                hours_needed, self._max_grid_price,
            )
            return False

        result = any(s <= now < e for s, e, _ in scheduled_windows)
        cheapest_prices = [f"{w[2]:.4f}" for w in scheduled_windows[:3]]
        log.debug(
            "Cheap grid: %d scheduled windows (cheapest: %s), now in window: %s",
            len(scheduled_windows), ", ".join(cheapest_prices), result,
        )
        return result

    # ------------------------------------------------------------------
    # Runtime setters (called by number / switch / select entities)
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def async_set_enabled(self, value: bool) -> None:
        self._enabled = value
        await self.async_refresh()

    async def async_set_mode(self, mode: str) -> None:
        log.info("Charging mode changed: %s → %s", self._mode, mode)
        self._mode = mode
        await self.async_refresh()

    @property
    def max_grid_power_w(self) -> int:
        return self._max_grid_power_w

    async def async_set_max_grid_power(self, value: int) -> None:
        log.info("Max grid power changed: %d → %d W", self._max_grid_power_w, value)
        self._max_grid_power_w = value
        await self.async_refresh()

    @property
    def charge_now_power_w(self) -> int:
        return self._charge_now_power_w

    async def async_set_charge_now_power(self, value: int) -> None:
        log.info("Charge power changed: %d → %d W", self._charge_now_power_w, value)
        self._charge_now_power_w = value
        await self.async_refresh()

    @property
    def target_soc_pct(self) -> float:
        return self._target_soc_pct

    async def async_set_target_soc(self, value: float) -> None:
        log.info("Target SoC changed: %.0f → %.0f %%", self._target_soc_pct, value)
        self._target_soc_pct = value
        await self.async_refresh()

    @property
    def max_grid_price(self) -> float:
        return self._max_grid_price

    async def async_set_max_grid_price(self, value: float) -> None:
        log.info("Max grid price changed: %.4f → %.4f", self._max_grid_price, value)
        self._max_grid_price = value
        await self.async_refresh()

    @property
    def min_charge_hours(self) -> int:
        return self._min_charge_hours

    async def async_set_min_charge_hours(self, value: int) -> None:
        log.info("Min charge hours changed: %d → %d", self._min_charge_hours, value)
        self._min_charge_hours = value
        await self.async_refresh()

    @property
    def peak_power_limit_w(self) -> int:
        return self._peak_power_limit_w

    async def async_set_peak_power_limit(self, value: int) -> None:
        log.info("Peak power limit changed: %d → %d W", self._peak_power_limit_w, value)
        self._peak_power_limit_w = value
        await self.async_refresh()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_sensor_float(self, entity_id: str | None, label: str) -> float | None:
        """Read a numeric sensor from the HA state machine. Returns float or None."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            log.warning("%s sensor '%s' not found in HA state machine", label, entity_id)
            return None
        if state.state in ("unavailable", "unknown", ""):
            log.debug("%s sensor '%s' is %s", label, entity_id, state.state)
            return None
        try:
            value = float(state.state)
            log.debug("%s sensor '%s' = %s", label, entity_id, state.state)
            return value
        except ValueError:
            log.error(
                "%s sensor '%s' has non-numeric state: %s",
                label, entity_id, state.state,
            )
            return None
