# ARGUS Simulation (Satellite Digital Twin)

`argus_simulation` generates synthetic 25-channel satellite telemetry and streams it to ARGUS backend over WebSocket.

## What It Does

1. Simulates ISS-like orbital telemetry at 1 Hz
2. Supports runtime attack/scenario modes
3. Receives control commands from backend (`set_mode`, `set_speed`)
4. Falls back to local CLI control for manual testing

## Telemetry Link

1. Outbound WebSocket target: `ws://localhost:8000/ws/sim`
2. Payload type: `telemetry`
3. Includes mode metadata so backend can track scenario context

## Supported Modes

1. `NORMAL`
2. `GPS_SPOOFING`
3. `SIGNAL_JAMMING`
4. `THERMAL_ANOMALY`
5. `POWER_DRAIN`
6. `DDOS`
7. `COMMAND_INJECTION`
8. `SENSOR_FREEZE`

## Local Setup

### Prerequisites

1. Python 3.10+
2. Backend running on port `8000`

### Install and run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python satellite_sim.py
```

## CLI Commands

While running, use interactive commands:

1. `normal`
2. `attack gps`
3. `attack jam`
4. `attack ddos`
5. `attack thermal`
6. `attack power`
7. `attack command`
8. `attack freeze`
9. `speed <multiplier>`
10. `status`

## Integration Expectations

1. Backend `/ws/sim` must be reachable before simulator can stream.
2. Dashboard controls call backend `/api/v1/simulation/control`, which forwards commands to this process.
3. If backend is offline, simulator keeps retrying with exponential backoff.

## Primary File

1. [`satellite_sim.py`](satellite_sim.py)
