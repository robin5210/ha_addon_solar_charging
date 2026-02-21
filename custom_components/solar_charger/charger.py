"""Async Modbus TCP interface for the Daheim Lader wallbox.

Register map (0-based PDU addresses as documented by Daheim Laden):
  Read (holding registers, FC3):
    0   - Charger status
    6   - L1 current (A)
    8   - L2 current (A)
    10  - L3 current (A)
    13  - Total charging power (W)

  Write (holding registers, FC6):
    91  - Current limit:    value = amps × 10  (60–160 for 6–16 A)
    93  - Charging mode:    1 = RFID/Modbus control (required for Modbus writes)
    95  - Charge command:   1 = Start, 2 = Stop
    186 - Phase mode:       1 = 1-phase, 3 = 3-phase

pymodbus 3.x async client: AsyncModbusTcpClient from pymodbus.client
"""

import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .const import CURRENT_LIMIT_MAX, CURRENT_LIMIT_MIN, REG_CHARGE_CMD, REG_CHARGING_MODE

log = logging.getLogger(__name__)


class DaheimCharger:
    def __init__(
        self,
        host: str,
        port: int,
        slave_id: int,
        phase_switch_register: int,
        phase_1_value: int,
        phase_3_value: int,
    ) -> None:
        self.host = host
        self.port = port
        self.slave_id = slave_id
        self.phase_switch_register = phase_switch_register
        self.phase_1_value = phase_1_value
        self.phase_3_value = phase_3_value
        self._client = AsyncModbusTcpClient(host=host, port=port, timeout=5)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to the Daheim Lader. Returns True on success."""
        try:
            connected = await self._client.connect()
            if connected:
                log.info(
                    "Connected to Daheim Lader at %s:%d (slave %d)",
                    self.host, self.port, self.slave_id,
                )
            else:
                log.error("Failed to connect to Daheim Lader at %s:%d", self.host, self.port)
            return connected
        except Exception as exc:  # noqa: BLE001
            log.error("Connection error: %s", exc)
            return False

    async def disconnect(self) -> None:
        self._client.close()
        log.info("Disconnected from Daheim Lader")

    def _is_connected(self) -> bool:
        return self._client.connected

    async def _ensure_connected(self) -> bool:
        """Return True if connected; attempt reconnect if dropped."""
        if self._is_connected():
            return True
        log.warning("Modbus connection lost, attempting reconnect...")
        return await self.connect()

    # ------------------------------------------------------------------
    # Low-level register I/O
    # ------------------------------------------------------------------

    async def _read_register(self, address: int) -> int | None:
        if not await self._ensure_connected():
            return None
        try:
            result = await self._client.read_holding_registers(
                address=address, count=1, device_id=self.slave_id
            )
            if result.isError():
                log.error("Modbus read error at register %d: %s", address, result)
                return None
            return result.registers[0]
        except ModbusException as exc:
            log.error("Modbus exception reading register %d: %s", address, exc)
            return None

    async def _write_register(self, address: int, value: int, retries: int = 3) -> bool:
        for attempt in range(retries):
            if not await self._ensure_connected():
                await asyncio.sleep(2)
                continue
            try:
                result = await self._client.write_register(
                    address=address, value=value, device_id=self.slave_id
                )
                if not result.isError():
                    log.debug("Wrote %d to register %d", value, address)
                    return True
                log.warning(
                    "Write to register %d returned error (attempt %d/%d): %s",
                    address, attempt + 1, retries, result,
                )
            except ModbusException as exc:
                log.error(
                    "Modbus exception writing register %d (attempt %d/%d): %s",
                    address, attempt + 1, retries, exc,
                )
            if attempt < retries - 1:
                await asyncio.sleep(0.5)
        return False

    # ------------------------------------------------------------------
    # High-level control commands
    # ------------------------------------------------------------------

    async def set_modbus_control_mode(self) -> bool:
        """Set charger to RFID/Modbus control mode (register 93 = 1)."""
        return await self._write_register(REG_CHARGING_MODE, 1)

    async def set_current_limit(self, amps: float) -> bool:
        """Set current limit. amps in range 6.0–16.0; written as amps × 10."""
        value = int(round(amps * 10))
        value = max(CURRENT_LIMIT_MIN, min(CURRENT_LIMIT_MAX, value))
        log.info("Setting current limit to %.1f A (register value %d)", amps, value)
        return await self._write_register(91, value)

    async def start_charging(self) -> bool:
        log.info("Sending start charge command")
        return await self._write_register(REG_CHARGE_CMD, 1)

    async def stop_charging(self) -> bool:
        log.info("Sending stop charge command")
        return await self._write_register(REG_CHARGE_CMD, 2)

    async def set_phase_mode(self, phases: int) -> bool:
        """Switch to 1-phase (phases=1) or 3-phase (phases=3)."""
        if phases == 1:
            value = self.phase_1_value
        elif phases == 3:
            value = self.phase_3_value
        else:
            raise ValueError(f"Invalid phase count: {phases}")
        log.info(
            "Setting phase mode to %d-phase (register %d = %d)",
            phases, self.phase_switch_register, value,
        )
        return await self._write_register(self.phase_switch_register, value)

    # ------------------------------------------------------------------
    # Status reads
    # ------------------------------------------------------------------

    async def get_status(self) -> int | None:
        return await self._read_register(0)

    async def get_phase_currents(self) -> tuple[float, float, float] | None:
        """Read L1/L2/L3 charging currents from registers 6, 8, 10.

        Register values are in tenths of Amps (÷10 = A). Returns (I_L1, I_L2, I_L3) or None
        on Modbus failure.
        """
        i1 = await self._read_register(6)
        i2 = await self._read_register(8)
        i3 = await self._read_register(10)
        if i1 is None or i2 is None or i3 is None:
            return None
        return i1 / 10, i2 / 10, i3 / 10
