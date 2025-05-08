#!/usr/bin/env python3
import sys
import time
import math
import sqlite3
import datetime
from pathlib import Path

sys.path.append("/sensos/lib")
from utils import read_kv_config, setup_logging

config = read_kv_config("/sensos/etc/i2c-sensors.conf")

DB_PATH = Path("/sensos/data/sensor_readings.db")


def ensure_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS i2c_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            device_address TEXT NOT NULL,
            sensor_type TEXT NOT NULL,
            key TEXT NOT NULL,
            value REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_i2c_time ON i2c_readings (timestamp)")
    conn.commit()


def read_bme280(addr_str: str = None):
    try:
        import board
        import busio
        from adafruit_bme280.basic import Adafruit_BME280_I2C

        addr = int(addr_str, 16)
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = Adafruit_BME280_I2C(i2c, address=addr)

        return {
            "temperature_c": round(sensor.temperature, 2),
            "humidity_percent": round(sensor.humidity, 2),
            "pressure_hpa": round(sensor.pressure, 2),
        }
    except Exception as e:
        print(f"⚠️ Error reading BME280: {e}", file=sys.stderr)
        return None


def read_ads1015(addr_str: str = None):
    try:
        import board
        import busio
        import adafruit_ads1x15.ads1015 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn

        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1015(i2c)

        return {
            "A0": round(AnalogIn(ads, ADS.P0).voltage, 3),
            "A1": round(AnalogIn(ads, ADS.P1).voltage, 3),
            "A2": round(AnalogIn(ads, ADS.P2).voltage, 3),
            "A3": round(AnalogIn(ads, ADS.P3).voltage, 3),
        }
    except Exception as e:
        print(f"⚠️ Error reading ADS1015: {e}", file=sys.stderr)
        return None


def read_scd30(addr_str: str = None):
    try:
        import board
        import busio
        import adafruit_scd30

        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_scd30.SCD30(i2c)

        if not sensor.data_available:
            return None

        return {
            "co2_ppm": round(sensor.CO2, 1),
            "temperature_c": round(sensor.temperature, 2),
            "humidity_percent": round(sensor.relative_humidity, 2),
        }
    except Exception as e:
        print(f"⚠️ Error reading SCD30: {e}", file=sys.stderr)
        return None


def read_scd4x(addr_str: str = None):
    try:
        import board
        import busio
        import adafruit_scd4x
        import time

        i2c = busio.I2C(board.SCL, board.SDA)
        scd = adafruit_scd4x.SCD4X(i2c)
        scd.start_periodic_measurement()
        time.sleep(5)  # give time for first reading

        if not scd.data_ready:
            return None

        return {
            "co2_ppm": round(scd.CO2, 1),
            "temperature_c": round(scd.temperature, 2),
            "humidity_percent": round(scd.relative_humidity, 2),
        }
    except Exception as e:
        print(f"⚠️ Error reading SCD4X: {e}", file=sys.stderr)
        return None


def read_i2c_gps(addr_str: str = None):
    # Placeholder — update based on actual hardware
    try:
        raise NotImplementedError("GPS reader not implemented")
    except Exception as e:
        print(f"⚠️ Error reading I2C GPS: {e}", file=sys.stderr)
        return None


def flatten_sensor_data(sensor_data, device_address, sensor_type, timestamp):
    if not sensor_data:
        return []
    return [
        (timestamp, device_address, sensor_type, key, float(value))
        for key, value in sensor_data.items()
    ]


def get_interval(key: str, default: int = 60) -> int:
    try:
        return int(config.get(key, default))
    except ValueError:
        return default



def main():
    setup_logging("read_i2c_sensors.log")

    sensors = [
        ("BME280_0x76", "0x76", "BME280", read_bme280),
        ("BME280_0x77", "0x77", "BME280", read_bme280),
        ("ADS1015", "0x48", "ADS1015", read_ads1015),
        ("SCD30", "0x61", "SCD30", read_scd30),
        ("SCD4X", "0x62", "SCD4X", read_scd4x),
        ("I2C_GPS", "0x10", "I2C_GPS", read_i2c_gps),
    ]

    last_run = {key: 0 for key, *_ in sensors}
    interval_sec = {key: get_interval(f"{key}_INTERVAL_SEC", 60) for key, *_ in sensors}

    print("🔁 Entering sensor loop")
    while True:
        now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        current_time = time.time()
        readings = []

        for key, addr, sensor_type, read_func in sensors:
            enabled = config.get(key, "").lower() == "true"
            if not enabled:
                continue
            if current_time - last_run[key] >= interval_sec[key]:
                print(f"⏱️ Polling {sensor_type} at {addr}")
                try:
                    data = read_func(addr)
                except Exception as e:
                    print(f"⚠️ Error polling {sensor_type}: {e}", file=sys.stderr)
                    data = None
                print(f"📟 {sensor_type} ({addr}) data: {data}")
                readings += flatten_sensor_data(data, addr, sensor_type, now) if data else []
                last_run[key] = current_time

        if readings:
            print(f"📝 Inserting {len(readings)} readings at {now}")
            conn = sqlite3.connect(DB_PATH)
            ensure_schema(conn)
            conn.executemany(
                """
                INSERT INTO i2c_readings (timestamp, device_address, sensor_type, key, value)
                VALUES (?, ?, ?, ?, ?)
                """,
                readings,
            )
            conn.commit()
            conn.close()

        # Compute time until next sensor is due
        next_due_in = min(
            max(0.5, interval_sec[key] - (current_time - last_run[key]))
            for key in last_run
            if config.get(key, "").lower() == "true"
        )
        time.sleep(min(5, math.ceil(next_due_in)))

if __name__ == "__main__":
    main()
