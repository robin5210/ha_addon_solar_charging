# Solar Surplus EV Charging

A Home Assistant custom integration that automatically charges your EV using solar surplus power via a **Daheim Lader** (DaheimLaden) wallbox controlled over Modbus TCP.

## Features

- Adjusts charging current dynamically to consume available solar surplus
- Switches between 1-phase and 3-phase charging automatically
- Enforces a configurable safety pause (default 2 minutes) when switching phases
- Hysteresis prevents rapid phase switching
- Enable/disable via a switch entity in HA
- Exposes status, current, and power sensors

## Installation via HACS

1. In HACS, go to **Integrations** → **Custom repositories**
2. Add this repository URL and select category **Integration**
3. Install **Solar Surplus EV Charging**
4. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Solar Surplus EV Charging**
3. Enter your Daheim Lader IP address and select your solar export power sensor
4. Configure charging thresholds on the next screen

## Requirements

- Daheim Lader wallbox reachable over the local network via Modbus TCP (default port 502)
- A Home Assistant sensor reporting solar export power in **Watts** (positive = exporting to grid)

## Modbus Register Map

| Register | Description |
|---|---|
| 91 | Current limit (write: amps × 10, range 60–160) |
| 93 | Control mode (write: 1 = Modbus control) |
| 95 | Charge command (write: 1 = Start, 2 = Stop) |
| 186 | Phase mode (write: 1 = 1-phase, 3 = 3-phase) |

> The phase switch register and values are configurable in case your hardware uses different values.

## Notes

- The Daheim Lader supports only one simultaneous Modbus TCP connection. If another client (e.g. EVCC) is also connected, use a Modbus TCP proxy.
- The solar export sensor must report in Watts. Negative values (importing from grid) are treated as 0 W.
