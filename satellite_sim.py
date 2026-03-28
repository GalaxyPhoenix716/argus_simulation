import time
import requests
import random
import threading
import sys
import numpy as np
from datetime import datetime
from skyfield.api import load, wgs84, EarthSatellite

# Configuration
FASTAPI_URL = "http://localhost:8000/telemetry"
SEND_TO_BACKEND = True  # Can be toggled

# 25-channel telemetry format per analysis
TELEMETRY_CHANNELS = [
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

class TelemetryFrame:
    """25-channel telemetry frame compatible with ARGUS format"""
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
        """Convert to dictionary format for API"""
        return {ch: getattr(self, ch) for ch in self.CHANNELS}

    def to_array(self):
        """Convert to 25-element numpy array"""
        return np.array([getattr(self, ch) for ch in self.CHANNELS], dtype=np.float64)

def add_noise(value, std):
    """Add Gaussian noise to a value"""
    return value + np.random.normal(0, std)

# Noise configuration from analysis defaults
NOISE_CONFIG = {
    'position_lat': 0.001 / 111.0,  # 0.001 km → degrees
    'position_lon': 0.001 / 111.0,
    'velocity_x': 0.0001,
    'velocity_y': 0.0001,
    'velocity_z': 0.0001,
    'attitude_roll': 0.01,
    'attitude_pitch': 0.01,
    'attitude_yaw': 0.01,
    'temperature': 0.1,
    'gyro_x': 0.001,
    'gyro_y': 0.001,
    'gyro_z': 0.001,
    'magnetometer_x': 0.1,
    'magnetometer_y': 0.1,
    'magnetometer_z': 0.1,
    'signal_strength': 0.5,
}

class SatelliteDigitalTwin:
    def __init__(self):
        # Orbit setup
        self.name = "ISS (ZARYA)"
        self.line1 = "1 25544U 98067A   24087.38016019  .00015947  00000-0  28497-3 0  9993"
        self.line2 = "2 25544  51.6416  163.7845 0004543  87.2514  34.0536 15.49520176445587"
        self.ts = load.timescale()
        self.satellite = EarthSatellite(self.line1, self.line2, self.name, self.ts)

        # Attack states
        self.current_mode = "NORMAL"

        # State variables for anomalies
        self.drift_lat = 0.0
        self.drift_lon = 0.0
        self.drift_alt = 0.0
        self.thermal_spike = 0.0
        self.battery_drain = 0.0
        self.adcs_chaos = 0.0

        # Baseline internal states
        self.battery_soc = 95.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.roll = 0.0

        # Frozen states for replay attack
        self.frozen_data = None

    def set_mode(self, mode):
        valid_modes = ["NORMAL", "GPS_SPOOFING", "SIGNAL_JAMMING", "THERMAL_ANOMALY", "POWER_DRAIN", "DDOS", "COMMAND_INJECTION", "SENSOR_FREEZE"]
        if mode in valid_modes:
            self.current_mode = mode
            print(f"\n[!] Attack Mode Changed To: {mode}")

            # Reset states on normal
            if mode == "NORMAL":
                self.drift_lat = 0.0
                self.drift_lon = 0.0
                self.drift_alt = 0.0
                self.thermal_spike = 0.0
                self.battery_drain = 0.0
                self.adcs_chaos = 0.0
                self.frozen_data = None
        else:
            print(f"\n[X] Invalid mode. Valid modes: {', '.join(valid_modes)}")

    def generate_telemetry(self):
        t = self.ts.now()
        geocentric = self.satellite.at(t)
        subpoint = wgs84.geographic_position_of(geocentric)

        # --- Attack Modifiers ---
        if self.current_mode == "GPS_SPOOFING":
            self.drift_lat += 0.05
            self.drift_lon -= 0.05
            self.drift_alt -= 0.1

        if self.current_mode == "THERMAL_ANOMALY":
            self.thermal_spike += 1.5

        if self.current_mode == "POWER_DRAIN":
            self.battery_drain += 0.5

        if self.current_mode == "COMMAND_INJECTION":
            self.adcs_chaos += random.uniform(-5.0, 5.0)

        # Base values (from original simulation)
        lat = subpoint.latitude.degrees + self.drift_lat
        lon = subpoint.longitude.degrees + self.drift_lon
        alt_km = subpoint.elevation.km + self.drift_alt
        alt_m = alt_km * 1000  # Convert km to meters for channel 5

        # Velocity approximation (km/s in ECI frame)
        vel_x = 7.66 + random.uniform(-0.01, 0.01)
        vel_y = 0.0 + random.uniform(-0.01, 0.01)
        vel_z = 0.0 + random.uniform(-0.01, 0.01)

        # Acceleration (gravity + small perturbations)
        acc_x = -7.0 + random.uniform(-0.1, 0.1)
        acc_y = 0.0 + random.uniform(-0.1, 0.1)
        acc_z = 0.0 + random.uniform(-0.1, 0.1)

        # Temperature (Celsius)
        temperature = 45.0 + random.uniform(-2, 2) + self.thermal_spike

        # Pressure (Pa) - exponential atmosphere model per analysis
        pressure = 101325 * np.exp(-alt_m / 8500000)

        # Humidity (0% in space, ~50% below 100km per analysis)
        humidity = 0.0 if alt_m > 100000 else 50.0

        # Power
        self.battery_soc = max(0.0, min(100.0, self.battery_soc - 0.01 - self.battery_drain))

        # Communication
        signal_strength = -65.0 + random.uniform(-5, 5)
        if self.current_mode == "SIGNAL_JAMMING":
            signal_strength = -110.0 + random.uniform(-10, 10)

        # ADCS
        self.pitch = 0.0 + random.uniform(-0.5, 0.5) + self.adcs_chaos
        self.yaw = 0.0 + random.uniform(-0.5, 0.5) + self.adcs_chaos
        self.roll = 0.0 + random.uniform(-0.5, 0.5) + self.adcs_chaos

        # Gyroscope (rad/s)
        gyro_x = random.uniform(-0.01, 0.01) + (self.adcs_chaos * 0.1)
        gyro_y = random.uniform(-0.01, 0.01)
        gyro_z = random.uniform(-0.01, 0.01)

        # Magnetometer (Earth dipole model, µT per analysis)
        # Simplified: vary with position and add noise
        mag_scale = 30.0  # Earth's magnetic field ~30-60 µT
        magnetometer_x = mag_scale + random.uniform(-5, 5)
        magnetometer_y = mag_scale * 0.5 + random.uniform(-2, 2)
        magnetometer_z = mag_scale * 0.8 + random.uniform(-3, 3)

        # Angular velocity (rad/s) - magnitude of gyro vector per analysis
        angular_velocity = np.sqrt(gyro_x**2 + gyro_y**2 + gyro_z**2)

        # Apply sensor noise from NOISE_CONFIG
        lat = add_noise(lat, NOISE_CONFIG['position_lat'])
        lon = add_noise(lon, NOISE_CONFIG['position_lon'])
        vel_x = add_noise(vel_x, NOISE_CONFIG['velocity_x'])
        vel_y = add_noise(vel_y, NOISE_CONFIG['velocity_y'])
        vel_z = add_noise(vel_z, NOISE_CONFIG['velocity_z'])
        temperature = add_noise(temperature, NOISE_CONFIG['temperature'])
        gyro_x = add_noise(gyro_x, NOISE_CONFIG['gyro_x'])
        gyro_y = add_noise(gyro_y, NOISE_CONFIG['gyro_y'])
        gyro_z = add_noise(gyro_z, NOISE_CONFIG['gyro_z'])
        magnetometer_x = add_noise(magnetometer_x, NOISE_CONFIG['magnetometer_x'])
        magnetometer_y = add_noise(magnetometer_y, NOISE_CONFIG['magnetometer_y'])
        magnetometer_z = add_noise(magnetometer_z, NOISE_CONFIG['magnetometer_z'])
        signal_strength = add_noise(signal_strength, NOISE_CONFIG['signal_strength'])

        # Create 25-channel telemetry frame per analysis
        timestamp = time.time()

        frame = TelemetryFrame(
            position_lat=lat,
            position_lon=lon,
            velocity_x=vel_x,
            velocity_y=vel_y,
            velocity_z=vel_z,
            altitude=alt_m,
            acceleration_x=acc_x,
            acceleration_y=acc_y,
            acceleration_z=acc_z,
            temperature=temperature,
            pressure=pressure,
            humidity=humidity,
            battery_level=self.battery_soc,
            signal_strength=signal_strength,
            gyro_x=gyro_x,
            gyro_y=gyro_y,
            gyro_z=gyro_z,
            magnetometer_x=magnetometer_x,
            magnetometer_y=magnetometer_y,
            magnetometer_z=magnetometer_z,
            attitude_roll=self.roll,
            attitude_pitch=self.pitch,
            attitude_yaw=self.yaw,
            angular_velocity=angular_velocity,
            timestamp=timestamp,
        )

        # Handle sensor freeze attack (keep original behavior)
        if self.current_mode == "SENSOR_FREEZE":
            if self.frozen_data is None:
                self.frozen_data = frame.to_dict()
                self.frozen_data["timestamp"] = timestamp
            else:
                frame = TelemetryFrame(**self.frozen_data)
                frame.timestamp = timestamp

        return frame

    def send_to_backend(self, telemetry):
        if not SEND_TO_BACKEND:
            return

        try:
            # Convert to dict for API
            data = telemetry.to_dict()
            response = requests.post(FASTAPI_URL, json=data, timeout=1.0)
            if response.status_code != 200:
                print(f" [!] Backend returned {response.status_code}")
        except requests.exceptions.RequestException as e:
            # print(f" [!] Failed to send data: {e}") # Too spammy if backend is offline
            pass

    def run_simulation_loop(self):
        print(f"📡 Generating 25-channel telemetry at 1Hz... Sending to {FASTAPI_URL}")
        print("💡 Hint: Use the terminal to change modes.")
        print("📊 Channels: position(2), velocity(3), altitude, acceleration(3), thermal(3), power(1), comms(1), gyro(3), magnetometer(3), attitude(3), angular_vel(1), timestamp(1) = 25 total")

        while True:
            try:
                frame = self.generate_telemetry()

                # Print a summary to console
                print(f"[{datetime.fromtimestamp(frame.timestamp).strftime('%H:%M:%S')}] Mode: {self.current_mode:<15} | "
                      f"Lat: {frame.position_lat:>8.4f} | Lon: {frame.position_lon:>8.4f} | "
                      f"Alt: {frame.altitude/1000:>8.2f}km | T: {frame.temperature:>5.1f}°C | "
                      f"Battery: {frame.battery_level:>5.1f}% | Signal: {frame.signal_strength:>6.1f}dBm")

                self.send_to_backend(frame)

                # Simulate DDOS delay
                if self.current_mode == "DDOS":
                    time.sleep(3)
                else:
                    time.sleep(1)

            except Exception as e:
                print(f"Simulation error: {e}")
                time.sleep(1)

def cli_listener(sim):
    print("--- 💻 Digital Twin Interactive Console ---")
    print("Commands:")
    print("  normal             -> Reset to NOMINAL state")
    print("  attack gps         -> GPS Spoofing / Orbital Drift")
    print("  attack jam         -> Signal Jamming (Comms loss)")
    print("  attack ddos        -> DDOS Attack (Saturate downlink)")
    print("  attack thermal     -> System Anomaly (Overheating)")
    print("  attack power       -> System Anomaly (Rapid Battery Drain)")
    print("  attack command     -> Command Injection (ADCS Chaos)")
    print("  attack freeze      -> Sensor Freeze (Replay Attack)")
    print("  toggle backend     -> Turn FastAPI streaming ON/OFF")
    print("-------------------------------------------\n")

    global SEND_TO_BACKEND

    while True:
        try:
            cmd = input("").strip().lower()
            if cmd == "normal":
                sim.set_mode("NORMAL")
            elif cmd == "attack gps":
                sim.set_mode("GPS_SPOOFING")
            elif cmd == "attack jam":
                sim.set_mode("SIGNAL_JAMMING")
            elif cmd == "attack ddos":
                sim.set_mode("DDOS")
            elif cmd == "attack thermal":
                sim.set_mode("THERMAL_ANOMALY")
            elif cmd == "attack power":
                sim.set_mode("POWER_DRAIN")
            elif cmd == "attack command":
                sim.set_mode("COMMAND_INJECTION")
            elif cmd == "attack freeze":
                sim.set_mode("SENSOR_FREEZE")
            elif cmd == "toggle backend":
                SEND_TO_BACKEND = not SEND_TO_BACKEND
                print(f"\n[!] Backend Streaming is now {'ON' if SEND_TO_BACKEND else 'OFF'}")
            elif cmd:
                print(f"\n[X] Unknown command: {cmd}")
        except KeyboardInterrupt:
            print("\nShutting down console...")
            sys.exit(0)

if __name__ == "__main__":
    twin = SatelliteDigitalTwin()

    # Start purely as a daemon thread so it dies with the main process
    sim_thread = threading.Thread(target=twin.run_simulation_loop, daemon=True)
    sim_thread.start()

    try:
        # The main thread blocks on the CLI listener
        cli_listener(twin)
    except KeyboardInterrupt:
        print("\nSimulation Stopped.")
        sys.exit(0)