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
T+0s    stop_charging()
T+0s    state → PHASE_SWITCH, record monotonic timestamp
T+0–N s return "paused" every update tick (coordinator still polls)
T+N s   write phase register → set_modbus_control_mode → set_current → start_charging
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto

log = logging.getLogger(__name__)

_MAX_MODBUS_FAILURES = 5


class ChargerState(Enum):
    IDLE = auto()
    CHARGING_1P = auto()
    CHARGING_3P = auto()
    PHASE_SWITCH = auto()
    ERROR = auto()


@dataclass
class ControllerConfig:
    min_current: int
    max_current: int
    voltage: int
    min_power_1phase: int
    min_power_3phase: int
    hysteresis_w: int
    phase_switch_pause: int  # seconds


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
        # From idle: strict thresholds, no hysteresis
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

        # Public properties read by the coordinator to populate entity states
        self.status: str = "idle"
        self.current_amps: float = 0.0
        self.power_watts: float = 0.0

    async def async_update(
        self,
        solar_export_w: float | None,
        enabled: bool,
        force_charging: bool = False,
    ) -> None:
        """Run one control iteration (called every update_interval seconds).

        When force_charging=True the controller bypasses the solar threshold check
        so that solar-assisted and charge-now modes always maintain charging.
        """
        log.debug(
            "FSM tick — state: %s, solar: %s W, enabled: %s, force: %s, phases: %d, amps: %.1f",
            self._state.name,
            f"{solar_export_w:.0f}" if solar_export_w is not None else "None",
            enabled,
            force_charging,
            self._current_phases,
            self._current_amps,
        )

        if force_charging:
            # Treat unavailable solar as 0 W — grid covers the rest
            solar_w = max(0.0, solar_export_w or 0.0)
        else:
            if solar_export_w is None:
                log.warning("Solar sensor unavailable — stopping charger")
                await self._safe_stop()
                return
            solar_w = max(0.0, solar_export_w)

        if self._state == ChargerState.IDLE:
            await self._handle_idle(solar_w, enabled, force_charging)
        elif self._state == ChargerState.CHARGING_1P:
            await self._handle_charging(solar_w, enabled, phases=1, force_charging=force_charging)
        elif self._state == ChargerState.CHARGING_3P:
            await self._handle_charging(solar_w, enabled, phases=3, force_charging=force_charging)
        elif self._state == ChargerState.PHASE_SWITCH:
            await self._handle_phase_switch(solar_w)
        elif self._state == ChargerState.ERROR:
            await self._handle_error()

    # ------------------------------------------------------------------
    # FSM state handlers
    # ------------------------------------------------------------------

    async def _handle_idle(self, solar_w: float, enabled: bool, force_charging: bool = False) -> None:
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

        amps = self._calc_amps(solar_w, target)
        log.info("Starting charging: %d-phase at %.1f A (solar %.0f W)", target, amps, solar_w)
        if await self._start_charging(target, amps):
            self._current_phases = target
            self._current_amps = amps
            self._state = ChargerState.CHARGING_1P if target == 1 else ChargerState.CHARGING_3P
            self._set_output(
                "charging_1p" if target == 1 else "charging_3p",
                amps,
                amps * target * self.cfg.voltage,
            )

    async def _handle_charging(self, solar_w: float, enabled: bool, phases: int, force_charging: bool = False) -> None:
        if not enabled:
            log.info("Addon disabled — stopping charger")
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
                await self._safe_stop()
                return

        if target != phases:
            log.info("Phase switch: %d → %d (solar %.0f W)", phases, target, solar_w)
            await self._begin_phase_switch(target)
            return

        amps = self._calc_amps(solar_w, phases)
        if amps != self._current_amps:
            log.debug("Adjusting current %.1f → %.1f A (solar %.0f W)", self._current_amps, amps, solar_w)
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

    async def _handle_phase_switch(self, solar_w: float) -> None:
        elapsed = time.monotonic() - self._phase_switch_start
        log.debug("Phase switch pause: %.0f / %d s", elapsed, self.cfg.phase_switch_pause)
        self._set_output("paused", 0.0, 0.0)

        if elapsed < self.cfg.phase_switch_pause:
            return

        amps = self._calc_amps(solar_w, self._target_phases)
        log.info("Phase switch complete — %d-phase at %.1f A", self._target_phases, amps)
        if await self._start_charging(self._target_phases, amps):
            self._current_phases = self._target_phases
            self._current_amps = amps
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
