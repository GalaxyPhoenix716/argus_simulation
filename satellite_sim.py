import time
import requests
import random
import threading
import sys
from datetime import datetime
from skyfield.api import load, wgs84, EarthSatellite

# Configuration
FASTAPI_URL = "http://localhost:8000/telemetry"
SEND_TO_BACKEND = True  # Can be toggled

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
            
        # Base values
        lat = subpoint.latitude.degrees + self.drift_lat
        lon = subpoint.longitude.degrees + self.drift_lon
        alt_km = subpoint.elevation.km + self.drift_alt
        # A simple velocity delta approximation since skyfield velocity is vector
        vel_kms = 7.66 + random.uniform(-0.01, 0.01)
        
        # Power
        sa_voltage = 110.0 + random.uniform(-2, 2)
        self.battery_soc = max(0.0, min(100.0, self.battery_soc - 0.01 - self.battery_drain))
        bat_temp = 20.0 + random.uniform(-1, 1) + (self.battery_drain * 2)
        
        # Thermal
        cpu_temp = 45.0 + random.uniform(-2, 2) + self.thermal_spike
        panel_temp = -50.0 + random.uniform(-5, 5) # Exposed to space
        payload_temp = 22.0 + random.uniform(-1, 1) + (self.thermal_spike * 0.5)
        
        # ADCS
        self.pitch = 0.0 + random.uniform(-0.5, 0.5) + self.adcs_chaos
        self.yaw = 0.0 + random.uniform(-0.5, 0.5) + self.adcs_chaos
        self.roll = 0.0 + random.uniform(-0.5, 0.5) + self.adcs_chaos
        gyro_x = random.uniform(-0.01, 0.01) + (self.adcs_chaos * 0.1)
        
        # Comms
        rssi = -65.0 + random.uniform(-5, 5)
        packet_loss = random.uniform(0.0, 0.5)
        
        if self.current_mode == "SIGNAL_JAMMING":
            rssi = -110.0 + random.uniform(-10, 10)
            packet_loss = random.uniform(20.0, 50.0)
            
        if self.current_mode == "DDOS":
            packet_loss = random.uniform(90.0, 99.0)
            
        data = {
            "timestamp": datetime.now().isoformat(),
            "status": self.current_mode,
            
            "navigation": {
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "alt_km": round(alt_km, 2),
                "vel_kms": round(vel_kms, 3)
            },
            "power": {
                "solar_array_voltage": round(sa_voltage, 2),
                "battery_soc": round(self.battery_soc, 2),
                "battery_temp": round(bat_temp, 2)
            },
            "thermal": {
                "cpu_temp": round(cpu_temp, 2),
                "panel_temp": round(panel_temp, 2),
                "payload_temp": round(payload_temp, 2)
            },
            "adcs": {
                "pitch": round(self.pitch, 2),
                "yaw": round(self.yaw, 2),
                "roll": round(self.roll, 2),
                "gyro_rate_x": round(gyro_x, 4)
            },
            "comms": {
                "rssi_dbm": round(rssi, 2),
                "packet_loss_pct": round(packet_loss, 2)
            }
        }
        
        if self.current_mode == "SENSOR_FREEZE":
            if self.frozen_data is None:
                self.frozen_data = data
            else:
                self.frozen_data["timestamp"] = datetime.now().isoformat()
                data = self.frozen_data

        return data

    def send_to_backend(self, telemetry):
        if not SEND_TO_BACKEND:
            return
            
        try:
            # We use timeout=1 to not block the simulation
            response = requests.post(FASTAPI_URL, json=telemetry, timeout=1.0)
            if response.status_code != 200:
                print(f" [!] Backend returned {response.status_code}")
        except requests.exceptions.RequestException as e:
            # print(f" [!] Failed to send data: {e}") # Too spammy if backend is offline
            pass

    def run_simulation_loop(self):
        print(f"📡 Generating highly realistic telemetry at 1Hz... Sending to {FASTAPI_URL}")
        print("💡 Hint: Use the terminal to change modes.")
        
        while True:
            try:
                data = self.generate_telemetry()
                
                # Print a summary to console
                nav = data['navigation']
                print(f"[{data['timestamp'][-12:-3]}] Mode: {self.current_mode:<15} | Lat: {nav['lat']:>8.4f} | Lon: {nav['lon']:>8.4f} | Alt: {nav['alt_km']:>8.2f} | CPU T: {data['thermal']['cpu_temp']:>5.1f} | Bat: {data['power']['battery_soc']:>5.1f}% | Pkt Loss: {data['comms']['packet_loss_pct']:>5.1f}%")
                
                self.send_to_backend(data)
                
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
