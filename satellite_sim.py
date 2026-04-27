"""
ARGUS Satellite Digital Twin Simulator
Connects to the ARGUS server via WebSocket and streams 25-channel telemetry
at 1 Hz. Receives control commands (attack modes, speed) from the server.
CLI is preserved for direct local control as a fallback.
"""

import time
import threading
import asyncio
import json
import sys
import random
import numpy as np
from datetime import datetime
from skyfield.api import load, wgs84, EarthSatellite

# ── Optional: install websockets if not present ───────────────────────────────
try:
    import websockets
except ImportError:
    print("[!] 'websockets' package not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_WS_URL = "wss://argus-server-970096522851.asia-south1.run.app/ws/sim"

# ── 25-channel frame ──────────────────────────────────────────────────────────
class TelemetryFrame:
    CHANNELS = [
        'position_lat', 'position_lon',
        'velocity_x', 'velocity_y', 'velocity_z',
        'altitude',
        'acceleration_x', 'acceleration_y', 'acceleration_z',
        'temperature', 'pressure', 'humidity',
        'battery_level', 'signal_strength',
        'gyro_x', 'gyro_y', 'gyro_z',
        'magnetometer_x', 'magnetometer_y', 'magnetometer_z',
        'attitude_roll', 'attitude_pitch', 'attitude_yaw',
        'angular_velocity', 'timestamp',
    ]

    def __init__(self, **kwargs):
        for ch in self.CHANNELS:
            setattr(self, ch, float(kwargs.get(ch, 0.0)))

    def to_dict(self):
        return {ch: getattr(self, ch) for ch in self.CHANNELS}


def add_noise(value, std):
    return value + np.random.normal(0, std)


NOISE_CONFIG = {
    'position_lat': 0.001 / 111.0,
    'position_lon': 0.001 / 111.0,
    'velocity_x': 0.0001, 'velocity_y': 0.0001, 'velocity_z': 0.0001,
    'attitude_roll': 0.01, 'attitude_pitch': 0.01, 'attitude_yaw': 0.01,
    'temperature': 0.1,
    'gyro_x': 0.001, 'gyro_y': 0.001, 'gyro_z': 0.001,
    'magnetometer_x': 0.1, 'magnetometer_y': 0.1, 'magnetometer_z': 0.1,
    'signal_strength': 0.5,
}

# ── WebSocket client (runs in its own asyncio loop / thread) ──────────────────
class SimWebSocketClient:
    """
    Maintains a persistent WebSocket connection to the ARGUS server.
    - Sends telemetry frames (thread-safe, called from sim loop)
    - Receives control messages and applies them to the digital twin
    """

    def __init__(self, twin: 'SatelliteDigitalTwin'):
        self.twin = twin
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False

    @property
    def connected(self):
        return self._connected

    def start(self):
        thread = threading.Thread(target=self._run_event_loop, daemon=True)
        thread.start()

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self):
        """Reconnect indefinitely with exponential back-off."""
        delay = 1
        while True:
            try:
                print(f"[WS] Connecting to {SERVER_WS_URL}...")
                async with websockets.connect(SERVER_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self._connected = True
                    delay = 1  # reset back-off
                    print("[WS] ✅ Connected to ARGUS server — streaming telemetry")

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            await self._handle_server_message(msg)
                        except json.JSONDecodeError:
                            pass

            except Exception as e:
                self._connected = False
                self._ws = None
                print(f"[WS] ❌ Disconnected: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _handle_server_message(self, msg: dict):
        """Process control messages from the server/dashboard."""
        if msg.get("type") != "control":
            return

        action = msg.get("action", "")

        if action == "set_mode":
            mode = (msg.get("mode") or "NORMAL").upper()
            self.twin.set_mode(mode)

        elif action == "set_speed":
            speed = float(msg.get("speed") or 1.0)
            self.twin.set_speed(speed)

        elif action == "stop":
            self.twin.set_mode("NORMAL")
            print("[!] Stop command received — returning to NORMAL")

    def send_telemetry(self, frame: dict, mode: str):
        """Thread-safe: send a telemetry frame from the sim loop thread."""
        if self._loop is None or self._ws is None or not self._connected:
            return
        message = json.dumps({
            "type": "telemetry",
            "payload": frame,
            "mode": mode,
        })
        asyncio.run_coroutine_threadsafe(self._safe_send(message), self._loop)

    async def _safe_send(self, message: str):
        try:
            if self._ws:
                await self._ws.send(message)
        except Exception as e:
            self._connected = False
            print(f"[WS] Send error: {e}")


# ── Digital twin ──────────────────────────────────────────────────────────────
class SatelliteDigitalTwin:
    def __init__(self):
        self.name = "ISS (ZARYA)"
        self.line1 = "1 25544U 98067A   24087.38016019  .00015947  00000-0  28497-3 0  9993"
        self.line2 = "2 25544  51.6416  163.7845 0004543  87.2514  34.0536 15.49520176445587"
        self.ts = load.timescale()
        self.satellite = EarthSatellite(self.line1, self.line2, self.name, self.ts)

        # Attack state
        self.current_mode = "NORMAL"
        self.speed_multiplier = 1.0

        # Drift states
        self.drift_lat = 0.0
        self.drift_lon = 0.0
        self.drift_alt = 0.0
        self.thermal_spike = 0.0
        self.battery_drain = 0.0
        self.adcs_chaos = 0.0

        # Baseline
        self.battery_soc = 95.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.roll = 0.0
        self.frozen_data = None

    def set_mode(self, mode: str):
        valid = [
            "NORMAL", "GPS_SPOOFING", "SIGNAL_JAMMING", "THERMAL_ANOMALY",
            "POWER_DRAIN", "DDOS", "COMMAND_INJECTION", "SENSOR_FREEZE",
        ]
        if mode not in valid:
            print(f"[X] Invalid mode. Valid: {', '.join(valid)}")
            return

        previous_mode = self.current_mode
        self.current_mode = mode
        print(f"\n[!] Mode → {mode}")

        if mode == "NORMAL":
            self.drift_lat = 0.0
            self.drift_lon = 0.0
            self.drift_alt = 0.0
            self.thermal_spike = 0.0
            self.battery_drain = 0.0
            self.adcs_chaos = 0.0
            self.frozen_data = None
        elif previous_mode == "SENSOR_FREEZE" and mode != "SENSOR_FREEZE":
            self.frozen_data = None

    def set_speed(self, speed: float):
        self.speed_multiplier = max(0.1, min(10.0, speed))
        print(f"[!] Speed → {self.speed_multiplier}x")

    def generate_telemetry(self) -> TelemetryFrame:
        t = self.ts.now()
        geocentric = self.satellite.at(t)
        subpoint = wgs84.geographic_position_of(geocentric)

        # ── Attack modifiers ──────────────────────────────────────────────────
        if self.current_mode == "GPS_SPOOFING":
            self.drift_lat = min(self.drift_lat + 0.02, 1.5)
            self.drift_lon = max(self.drift_lon - 0.02, -1.5)
            self.drift_alt = max(self.drift_alt - 0.05, -3.0)
        else:
            self.drift_lat *= 0.9
            self.drift_lon *= 0.9
            self.drift_alt *= 0.9

        if self.current_mode == "THERMAL_ANOMALY":
            self.thermal_spike = min(self.thermal_spike + 1.0, 35.0)
        else:
            self.thermal_spike *= 0.85

        if self.current_mode == "POWER_DRAIN":
            self.battery_drain = min(self.battery_drain + 0.06, 0.30)
        else:
            self.battery_drain *= 0.5

        if self.current_mode == "COMMAND_INJECTION":
            self.adcs_chaos = float(np.clip(self.adcs_chaos + random.uniform(-2.0, 2.0), -25.0, 25.0))
        else:
            self.adcs_chaos *= 0.8

        # ── Base values ───────────────────────────────────────────────────────
        lat = subpoint.latitude.degrees + self.drift_lat
        lon = subpoint.longitude.degrees + self.drift_lon
        alt_km = subpoint.elevation.km + self.drift_alt
        alt_m = alt_km * 1000

        vel_x = 7.66 + random.uniform(-0.01, 0.01)
        vel_y = random.uniform(-0.01, 0.01)
        vel_z = random.uniform(-0.01, 0.01)

        acc_x = -7.0 + random.uniform(-0.1, 0.1)
        acc_y = random.uniform(-0.1, 0.1)
        acc_z = random.uniform(-0.1, 0.1)

        temperature = 45.0 + random.uniform(-2, 2) + self.thermal_spike
        pressure = 101325 * np.exp(-alt_m / 8500000)
        humidity = 0.0 if alt_m > 100000 else 50.0

        self.battery_soc = max(0.0, min(100.0, self.battery_soc - 0.01 - self.battery_drain))

        signal_strength = -65.0 + random.uniform(-5, 5)
        if self.current_mode == "SIGNAL_JAMMING":
            signal_strength = -110.0 + random.uniform(-10, 10)

        self.pitch = random.uniform(-0.5, 0.5) + self.adcs_chaos
        self.yaw   = random.uniform(-0.5, 0.5) + self.adcs_chaos
        self.roll  = random.uniform(-0.5, 0.5) + self.adcs_chaos

        gyro_x = random.uniform(-0.01, 0.01) + (self.adcs_chaos * 0.1)
        gyro_y = random.uniform(-0.01, 0.01)
        gyro_z = random.uniform(-0.01, 0.01)

        mag_scale = 30.0
        magnetometer_x = mag_scale + random.uniform(-5, 5)
        magnetometer_y = mag_scale * 0.5 + random.uniform(-2, 2)
        magnetometer_z = mag_scale * 0.8 + random.uniform(-3, 3)

        angular_velocity = np.sqrt(gyro_x**2 + gyro_y**2 + gyro_z**2)

        # ── Add sensor noise ──────────────────────────────────────────────────
        lat             = add_noise(lat, NOISE_CONFIG['position_lat'])
        lon             = add_noise(lon, NOISE_CONFIG['position_lon'])
        vel_x           = add_noise(vel_x, NOISE_CONFIG['velocity_x'])
        vel_y           = add_noise(vel_y, NOISE_CONFIG['velocity_y'])
        vel_z           = add_noise(vel_z, NOISE_CONFIG['velocity_z'])
        temperature      = add_noise(temperature, NOISE_CONFIG['temperature'])
        gyro_x          = add_noise(gyro_x, NOISE_CONFIG['gyro_x'])
        gyro_y          = add_noise(gyro_y, NOISE_CONFIG['gyro_y'])
        gyro_z          = add_noise(gyro_z, NOISE_CONFIG['gyro_z'])
        magnetometer_x  = add_noise(magnetometer_x, NOISE_CONFIG['magnetometer_x'])
        magnetometer_y  = add_noise(magnetometer_y, NOISE_CONFIG['magnetometer_y'])
        magnetometer_z  = add_noise(magnetometer_z, NOISE_CONFIG['magnetometer_z'])
        signal_strength = add_noise(signal_strength, NOISE_CONFIG['signal_strength'])

        timestamp = time.time()

        frame = TelemetryFrame(
            position_lat=lat, position_lon=lon,
            velocity_x=vel_x, velocity_y=vel_y, velocity_z=vel_z,
            altitude=alt_m,
            acceleration_x=acc_x, acceleration_y=acc_y, acceleration_z=acc_z,
            temperature=temperature, pressure=pressure, humidity=humidity,
            battery_level=self.battery_soc, signal_strength=signal_strength,
            gyro_x=gyro_x, gyro_y=gyro_y, gyro_z=gyro_z,
            magnetometer_x=magnetometer_x, magnetometer_y=magnetometer_y,
            magnetometer_z=magnetometer_z,
            attitude_roll=self.roll, attitude_pitch=self.pitch, attitude_yaw=self.yaw,
            angular_velocity=angular_velocity,
            timestamp=timestamp,
        )

        # ── Sensor freeze (replay attack) ─────────────────────────────────────
        if self.current_mode == "SENSOR_FREEZE":
            if self.frozen_data is None:
                self.frozen_data = frame.to_dict()
            frozen = TelemetryFrame(**self.frozen_data)
            frozen.timestamp = timestamp
            return frozen

        return frame

    def run_simulation_loop(self, ws_client: 'SimWebSocketClient'):
        print("📡 Starting 1Hz telemetry stream...")
        print(f"   → Sending to {SERVER_WS_URL}")
        print("   → Use CLI commands to control attack modes\n")

        while True:
            try:
                frame = self.generate_telemetry()
                frame_dict = frame.to_dict()

                # Console summary
                print(
                    f"[{datetime.fromtimestamp(frame.timestamp).strftime('%H:%M:%S')}] "
                    f"Mode: {self.current_mode:<18} | "
                    f"Lat: {frame.position_lat:>8.4f}° | Lon: {frame.position_lon:>8.4f}° | "
                    f"Alt: {frame.altitude/1000:>7.2f}km | "
                    f"T: {frame.temperature:>5.1f}°C | "
                    f"Bat: {frame.battery_level:>5.1f}% | "
                    f"Sig: {frame.signal_strength:>6.1f}dBm | "
                    f"WS: {'🟢' if ws_client.connected else '🔴'}"
                )

                ws_client.send_telemetry(frame_dict, self.current_mode)

                # DDOS slows the loop
                sleep_time = (3.0 if self.current_mode == "DDOS" else 1.0) / self.speed_multiplier
                time.sleep(sleep_time)

            except Exception as e:
                print(f"[!] Simulation error: {e}")
                time.sleep(1)


# ── CLI ───────────────────────────────────────────────────────────────────────
def cli_listener(twin: SatelliteDigitalTwin):
    print("═" * 50)
    print("   ARGUS Digital Twin — Interactive Console")
    print("═" * 50)
    print("  normal              → Reset to NOMINAL state")
    print("  attack gps          → GPS Spoofing / Orbital Drift")
    print("  attack jam          → Signal Jamming")
    print("  attack ddos         → DDoS (slow downlink)")
    print("  attack thermal      → Thermal Anomaly")
    print("  attack power        → Battery Drain")
    print("  attack command      → Command Injection (ADCS)")
    print("  attack freeze       → Sensor Freeze / Replay")
    print("  speed <multiplier>  → Set speed (e.g. speed 2)")
    print("  status              → Show current state")
    print("═" * 50 + "\n")

    CMD_MAP = {
        "normal":         ("NORMAL", None),
        "attack gps":     ("GPS_SPOOFING", None),
        "attack jam":     ("SIGNAL_JAMMING", None),
        "attack ddos":    ("DDOS", None),
        "attack thermal": ("THERMAL_ANOMALY", None),
        "attack power":   ("POWER_DRAIN", None),
        "attack command": ("COMMAND_INJECTION", None),
        "attack freeze":  ("SENSOR_FREEZE", None),
    }

    while True:
        try:
            cmd = input("").strip().lower()

            if cmd in CMD_MAP:
                mode, _ = CMD_MAP[cmd]
                twin.set_mode(mode)

            elif cmd.startswith("speed "):
                try:
                    s = float(cmd.split()[1])
                    twin.set_speed(s)
                except (IndexError, ValueError):
                    print("[X] Usage: speed <float>")

            elif cmd == "status":
                print(f"  Mode:  {twin.current_mode}")
                print(f"  Speed: {twin.speed_multiplier}x")
                print(f"  Battery: {twin.battery_soc:.1f}%")

            elif cmd:
                print(f"[X] Unknown command: '{cmd}'")

        except (KeyboardInterrupt, EOFError):
            print("\n[!] Shutting down simulation...")
            sys.exit(0)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    twin = SatelliteDigitalTwin()
    ws_client = SimWebSocketClient(twin)

    # Start WebSocket client in background thread
    ws_client.start()

    # Start simulation loop in background thread
    sim_thread = threading.Thread(
        target=twin.run_simulation_loop,
        args=(ws_client,),
        daemon=True,
    )
    sim_thread.start()

    # Main thread = CLI
    try:
        cli_listener(twin)
    except KeyboardInterrupt:
        print("\n[!] Simulation stopped.")
        sys.exit(0)
