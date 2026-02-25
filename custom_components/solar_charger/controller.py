"""Solar surplus charging controller — async finite state machine.

States
------
IDLE          No charging (insufficient solar, disabled, or no car).
CHARGING_1P   Actively charging in 1-phase mode.
CHARGING_3P   Actively charging in 3-phase mode.
PHASE_SWITCH  Waiting out the mandatory pause between phase changes.
ERROR         Consecutive Modbus failures exceeded threshold; writes suspended.

Phase selection (with hysteresis when already charging)
--------------------------------------------------------
  solar >= min_power_3phase + hysteresis_w  →  3-phase
  solar >= min_power_1phase                 →  1-phase
  solar <  min_power_1phase - hysteresis_w  →  stop / IDLE

Phase switch sequence
---------------------
T+0s    pause_charging() — sets current limit to 0 to keep session alive
T+0s    state → PHASE_SWITCH, record monotonic timestamp
T+0–N s return "paused" every update tick (coordinator still polls)
T+N s   write phase register → set_modbus_control_mode → set_current → start_charging

Peak shaving
------------
When peak_power_limit_w > 0 and a grid import sensor is configured, the
controller limits charging current so that grid import stays within the cap.
If the available headroom drops below the minimum charging power the session
is paused (current = 0, session kept alive) until headroom recovers.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto

from .const import (
    CHARGING_MODE_CHARGE_NOW,
    CHARGING_MODE_CHEAP_GRID,
    CHARGING_MODE_SOLAR_ASSISTED,
)

log = logging.getLogger(__name__)

_MAX_MODBUS_FAILURES = 5

_CHARGING_STATES = frozenset({})  # filled after class definition


class ChargerState(Enum):
    IDLE = auto()
    CHARGING_1P = auto()
    CHARGING_3P = auto()
    PHASE_SWITCH = auto()
    ERROR = auto()


_CHARGING_STATES = frozenset({ChargerState.CHARGING_1P, ChargerState.CHARGING_3P})


@dataclass
class ControllerConfig:
    min_current: int
    max_current: int
    voltage: int
    min_power_1phase: int
    min_power_3phase: int
    hysteresis_w: int
    phase_switch_pause: int  # seconds


@dataclass
class ChargeInput:
    """All context the coordinator knows about the current situation.

    The controller resolves mode-specific logic internally so that the
    coordinator only needs to gather facts, not make charging decisions.
    """
    solar_w: float | None       # raw solar export reading; None = sensor unavailable
    mode: str                   # CHARGING_MODE_* constant from const.py
    enabled: bool
    max_grid_power_w: int       # max grid contribution for solar_assisted mode
    charge_now_power_w: int     # fixed target power for charge_now / cheap_grid modes
    # Cheap-grid mode fields
    scheduled_charge: bool = False          # True if coordinator says this hour is scheduled
    current_soc_pct: float | None = None   # car battery SoC %; None = sensor unavailable
    target_soc_pct: float = 80.0           # stop grid charging above this SoC
    # Peak shaving / capacity tariff (applies to all modes)
    grid_import_w: float | None = None     # current total household grid import; None = disabled
    peak_power_limit_w: int = 0            # 0 = disabled; >0 = hard cap on grid import (W)


def _target_phase(
    solar_w: float,
    current_phase: int,  # 0 = idle
    min_1p: int,
    min_3p: int,
    hyst: int,
) -> int | None:
    """Return target phase (1 or 3) or None (= stop).

    Hysteresis applies only when already charging to prevent rapid toggling.
    """
    if current_phase == 0:
        if solar_w >= min_3p:
            return 3
        if solar_w >= min_1p:
            return 1
        return None

    if current_phase == 1:
        if solar_w >= min_3p + hyst:
            return 3
        if solar_w < min_1p - hyst:
            return None
        return 1

    # current_phase == 3
    if solar_w < min_3p - hyst:
        return 1 if solar_w >= min_1p else None
    return 3


class Controller:
    """Async FSM that drives a DaheimCharger based on available solar surplus."""

    def __init__(self, charger, cfg: ControllerConfig) -> None:
        self._charger = charger
        self.cfg = cfg

        self._state = ChargerState.IDLE
        self._current_phases: int = 0
        self._target_phases: int = 1
        self._current_amps: float = 0.0
        self._phase_switch_start: float = 0.0
        self._modbus_failures: int = 0
        # Set when we deliberately pause charging (peak shaving); prevents
        # _sync_charger_state from mistaking it for an external stop.
        self._intentionally_paused: bool = False

        # Peak shaving state, updated at the start of each tick
        self._peak_power_limit_w: int = 0
        self._grid_import_w: float | None = None

        # Public properties read by the coordinator to populate entity states
        self.status: str = "idle"
        self.current_amps: float = 0.0    # commanded
        self.power_watts: float = 0.0     # commanded
        self.measured_amps: float = 0.0   # from phase current registers
        self.measured_power_w: float = 0.0

    # ------------------------------------------------------------------
    # Mode resolution
    # ------------------------------------------------------------------

    def _resolve_mode(self, inp: ChargeInput) -> tuple[float | None, float | None, bool]:
        """Resolve the charging context into controller parameters.

        Returns (threshold_w, calc_w, force_charging):
          threshold_w    – power compared against start/stop thresholds.
          calc_w         – power used for current calculation; None → use threshold_w.
          force_charging – when True, bypass threshold check (always try to charge).
        """
        if inp.mode == CHARGING_MODE_CHARGE_NOW:
            return float(inp.charge_now_power_w), None, True

        if inp.mode == CHARGING_MODE_SOLAR_ASSISTED:
            if inp.solar_w is None:
                return None, None, False  # sensor unavailable → stop
            threshold = inp.solar_w + inp.max_grid_power_w
            return threshold, inp.solar_w, False  # speed = solar only, grid fills floor

        if inp.mode == CHARGING_MODE_CHEAP_GRID:
            if not inp.scheduled_charge:
                # Either not in a cheap hour or SoC target already reached
                return None, None, False
            return float(inp.charge_now_power_w), None, True

        # solar_only (default)
        return inp.solar_w, None, False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def async_update(self, inp: ChargeInput) -> None:
        """Run one control iteration (called every update_interval seconds)."""
        # -- 1. Capture peak shaving context for this tick ----------------------
        self._peak_power_limit_w = inp.peak_power_limit_w
        self._grid_import_w = inp.grid_import_w

        # -- 2. Read actual charger state to detect external starts/stops -------
        await self._sync_charger_state()

        # -- 3. Resolve mode-specific parameters --------------------------------
        threshold_w, calc_w_raw, force_charging = self._resolve_mode(inp)
        calc_w = calc_w_raw if calc_w_raw is not None else threshold_w

        log.debug(
            "FSM tick — state: %s, mode: %s, solar: %s W, threshold: %s W, "
            "calc: %s W, enabled: %s, force: %s, phases: %d, amps: %.1f, "
            "grid_import: %s W, peak_limit: %d W",
            self._state.name, inp.mode,
            f"{inp.solar_w:.0f}" if inp.solar_w is not None else "None",
            f"{threshold_w:.0f}" if threshold_w is not None else "None",
            f"{calc_w:.0f}" if calc_w is not None else "None",
            inp.enabled, force_charging,
            self._current_phases, self._current_amps,
            f"{inp.grid_import_w:.0f}" if inp.grid_import_w is not None else "None",
            inp.peak_power_limit_w,
        )

        # -- 4. Resolve effective solar for FSM ---------------------------------
        if force_charging:
            solar_w: float = max(0.0, threshold_w or 0.0)
            eff_calc_w: float = max(0.0, calc_w or 0.0)
        else:
            if threshold_w is None:
                log.warning("Charging not possible (sensor unavailable or conditions not met) — stopping")
                await self._safe_stop()
                return
            solar_w = max(0.0, threshold_w)
            eff_calc_w = max(0.0, calc_w or 0.0)

        # -- 5. Run FSM state handler -------------------------------------------
        if self._state == ChargerState.IDLE:
            await self._handle_idle(solar_w, inp.enabled, force_charging, eff_calc_w)
        elif self._state == ChargerState.CHARGING_1P:
            await self._handle_charging(solar_w, inp.enabled, phases=1, force_charging=force_charging, calc_w=eff_calc_w)
        elif self._state == ChargerState.CHARGING_3P:
            await self._handle_charging(solar_w, inp.enabled, phases=3, force_charging=force_charging, calc_w=eff_calc_w)
        elif self._state == ChargerState.PHASE_SWITCH:
            await self._handle_phase_switch(eff_calc_w)
        elif self._state == ChargerState.ERROR:
            await self._handle_error()

    # ------------------------------------------------------------------
    # External-state sync
    # ------------------------------------------------------------------

    async def _sync_charger_state(self) -> None:
        """Read actual phase currents and reconcile with FSM state.

        Detects sessions started or stopped by external sources (RFID card,
        charger app) so the FSM stays consistent with reality.
        Intentional pauses (peak shaving) are exempt from the external-stop check.
        """
        currents = await self._charger.get_phase_currents()
        if currents is None:
            return

        i1, i2, i3 = currents
        total_a = i1 + i2 + i3
        charger_active = total_a > 0.1

        self.measured_amps = max(i1, i2, i3)
        self.measured_power_w = total_a * self.cfg.voltage

        if self._state in _CHARGING_STATES and not charger_active:
            if self._intentionally_paused:
                pass  # We paused it deliberately — not an external stop
            else:
                log.warning(
                    "Charger stopped externally (measured 0 A while FSM was %s) — resetting to IDLE",
                    self._state.name,
                )
                self._current_phases = 0
                self._current_amps = 0.0
                self._state = ChargerState.IDLE
                self._set_output("idle", 0.0, 0.0)

        elif self._state == ChargerState.IDLE and charger_active:
            log.info(
                "External charging detected — L1: %.1f A, L2: %.1f A, L3: %.1f A",
                i1, i2, i3,
            )
            active_phases = sum(1 for i in (i1, i2, i3) if i > 0.1)
            if active_phases >= 2:
                self._current_phases = 3
                self._state = ChargerState.CHARGING_3P
                self._set_output("charging_3p", max(i1, i2, i3), total_a * self.cfg.voltage)
            else:
                self._current_phases = 1
                self._state = ChargerState.CHARGING_1P
                self._set_output("charging_1p", max(i1, i2, i3), total_a * self.cfg.voltage)

    # ------------------------------------------------------------------
    # Peak shaving
    # ------------------------------------------------------------------

    def _apply_peak_limit(self, desired_amps: float, phases: int) -> float:
        """Cap charging amps to stay within the capacity tariff peak power limit.

        Returns the (potentially reduced) amps, or 0.0 to signal 'pause session'.
        Net grid import is calculated by removing the current EV charger contribution
        so we correctly handle the case where we are already charging at some level.
        """
        if self._peak_power_limit_w <= 0 or self._grid_import_w is None:
            return desired_amps

        current_charge_w = self._current_amps * phases * self.cfg.voltage
        net_import_w = self._grid_import_w - current_charge_w
        headroom_w = self._peak_power_limit_w - net_import_w

        min_charge_w = self.cfg.min_current * phases * self.cfg.voltage
        if headroom_w < min_charge_w:
            log.info(
                "Peak limit: grid import %.0f W, headroom %.0f W < minimum %.0f W — pausing",
                self._grid_import_w, headroom_w, min_charge_w,
            )
            return 0.0

        peak_amps = headroom_w / (phases * self.cfg.voltage)
        if peak_amps < desired_amps:
            log.info(
                "Peak limit: reducing charge %.1f A → %.1f A "
                "(import %.0f W, headroom %.0f W, limit %d W)",
                desired_amps, peak_amps,
                self._grid_import_w, headroom_w, self._peak_power_limit_w,
            )
        return min(desired_amps, peak_amps)

    # ------------------------------------------------------------------
    # FSM state handlers
    # ------------------------------------------------------------------

    async def _handle_idle(self, solar_w: float, enabled: bool, force_charging: bool, calc_w: float) -> None:
        if not enabled:
            self._set_output("idle", 0.0, 0.0)
            return

        if force_charging:
            target = 3 if solar_w >= self.cfg.min_power_3phase else 1
            log.debug("Idle (forced): solar %.0f W → %d-phase", solar_w, target)
        else:
            target = _target_phase(
                solar_w, 0, self.cfg.min_power_1phase,
                self.cfg.min_power_3phase, self.cfg.hysteresis_w,
            )
            log.debug(
                "Idle: solar %.0f W, 1p threshold %d W, 3p threshold %d W → target phase: %s",
                solar_w, self.cfg.min_power_1phase, self.cfg.min_power_3phase,
                target if target is not None else "none (below threshold)",
            )
            if target is None:
                self._set_output("idle", 0.0, 0.0)
                return

        amps = self._calc_amps(calc_w, target)
        amps = self._apply_peak_limit(amps, target)
        if amps == 0.0:
            log.info("Peak power limit: not starting charging (insufficient headroom)")
            self._set_output("idle", 0.0, 0.0)
            return

        log.info(
            "Starting charging: %d-phase at %.1f A (threshold %.0f W, calc %.0f W)",
            target, amps, solar_w, calc_w,
        )
        if await self._start_charging(target, amps):
            self._current_phases = target
            self._current_amps = amps
            self._intentionally_paused = False
            self._state = ChargerState.CHARGING_1P if target == 1 else ChargerState.CHARGING_3P
            self._set_output(
                "charging_1p" if target == 1 else "charging_3p",
                amps,
                amps * target * self.cfg.voltage,
            )

    async def _handle_charging(self, solar_w: float, enabled: bool, phases: int, force_charging: bool, calc_w: float) -> None:
        if not enabled:
            log.info("Charging disabled — stopping charger")
            self._intentionally_paused = False
            await self._safe_stop()
            return

        if force_charging:
            target = 3 if solar_w >= self.cfg.min_power_3phase else 1
        else:
            target = _target_phase(
                solar_w, phases, self.cfg.min_power_1phase,
                self.cfg.min_power_3phase, self.cfg.hysteresis_w,
            )
            if target is None:
                log.info("Insufficient solar (%.0f W) — stopping charger", solar_w)
                self._intentionally_paused = False
                await self._safe_stop()
                return

        if target != phases:
            log.info("Phase switch: %d → %d (solar %.0f W)", phases, target, solar_w)
            self._intentionally_paused = False
            await self._begin_phase_switch(target)
            return

        amps = self._calc_amps(calc_w, phases)
        amps = self._apply_peak_limit(amps, phases)

        if amps == 0.0:
            # Peak limit triggered — pause session without ending it
            if not self._intentionally_paused:
                log.info("Peak power limit reached — pausing charging session")
                await self._charger.pause_charging()
                self._current_amps = 0.0
                self._intentionally_paused = True
            self._set_output("paused", 0.0, 0.0)
            return

        # Resuming from peak-pause or adjusting current
        if self._intentionally_paused:
            log.info("Peak headroom recovered — resuming charging at %.1f A", amps)
            self._intentionally_paused = False

        if amps != self._current_amps:
            log.debug(
                "Adjusting current %.1f → %.1f A (threshold %.0f W, calc %.0f W)",
                self._current_amps, amps, solar_w, calc_w,
            )
            if not await self._charger.set_current_limit(amps):
                self._on_modbus_failure()
                return
            self._current_amps = amps
            self._modbus_failures = 0

        self._set_output(
            "charging_1p" if phases == 1 else "charging_3p",
            self._current_amps,
            self._current_amps * phases * self.cfg.voltage,
        )

    async def _handle_phase_switch(self, calc_w: float) -> None:
        elapsed = time.monotonic() - self._phase_switch_start
        log.debug("Phase switch pause: %.0f / %d s", elapsed, self.cfg.phase_switch_pause)
        self._set_output("paused", 0.0, 0.0)

        if elapsed < self.cfg.phase_switch_pause:
            return

        amps = self._calc_amps(calc_w, self._target_phases)
        amps = self._apply_peak_limit(amps, self._target_phases)
        if amps == 0.0:
            log.info("Phase switch complete but peak limit active — staying paused")
            return

        log.info("Phase switch complete — %d-phase at %.1f A", self._target_phases, amps)
        if await self._start_charging(self._target_phases, amps):
            self._current_phases = self._target_phases
            self._current_amps = amps
            self._intentionally_paused = False
            self._state = (
                ChargerState.CHARGING_1P if self._target_phases == 1 else ChargerState.CHARGING_3P
            )
            self._set_output(
                "charging_1p" if self._target_phases == 1 else "charging_3p",
                amps,
                amps * self._target_phases * self.cfg.voltage,
            )

    async def _handle_error(self) -> None:
        log.warning("In ERROR state — attempting Modbus recovery")
        if await self._charger.get_status() is not None:
            log.info("Modbus recovered — returning to IDLE")
            self._modbus_failures = 0
            self._state = ChargerState.IDLE
        self._set_output("error", 0.0, 0.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _begin_phase_switch(self, target: int) -> None:
        await self._charger.pause_charging()
        self._target_phases = target
        self._phase_switch_start = time.monotonic()
        self._current_amps = 0.0
        self._state = ChargerState.PHASE_SWITCH
        self._set_output("paused", 0.0, 0.0)

    async def _start_charging(self, phases: int, amps: float) -> bool:
        if not await self._charger.set_modbus_control_mode():
            self._on_modbus_failure()
            return False
        if not await self._charger.set_phase_mode(phases):
            self._on_modbus_failure()
            return False
        if not await self._charger.set_current_limit(amps):
            self._on_modbus_failure()
            return False
        if not await self._charger.start_charging():
            self._on_modbus_failure()
            return False
        self._modbus_failures = 0
        return True

    async def _safe_stop(self) -> None:
        await self._charger.stop_charging()
        self._current_amps = 0.0
        self._current_phases = 0
        self._state = ChargerState.IDLE
        self._set_output("idle", 0.0, 0.0)

    def _calc_amps(self, solar_w: float, phases: int) -> float:
        raw = solar_w / (phases * self.cfg.voltage)
        return round(
            max(float(self.cfg.min_current), min(float(self.cfg.max_current), raw)),
            1,
        )

    def _on_modbus_failure(self) -> None:
        self._modbus_failures += 1
        log.error("Modbus failure #%d / %d", self._modbus_failures, _MAX_MODBUS_FAILURES)
        if self._modbus_failures >= _MAX_MODBUS_FAILURES:
            log.error("Too many Modbus failures — entering ERROR state")
            self._state = ChargerState.ERROR
            self._set_output("error", 0.0, 0.0)

    def _set_output(self, status: str, current_a: float, power_w: float) -> None:
        self.status = status
        self.current_amps = current_a
        self.power_watts = power_w
