#!/usr/bin/env python3
import os
import sys
import json
import sqlite3
import datetime
from pathlib import Path

sys.path.append("/sensos/lib")
from utils import load_defaults, setup_logging

CONFIG_SECTION = "i2c-sensors"
DB_PATH = Path("/sensos/data/sensor_readings.db")


def ensure_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            temperature_c REAL,
            humidity_percent REAL,
            pressure_hpa REAL,
            scd30_data TEXT,
            scd4x_data TEXT,
            ads1015_data TEXT,
            i2c_gps_data TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timestamp ON sensor_readings (timestamp)"
    )
    conn.commit()


def read_bme280(addr_str: str):
    try:
        import board
        import busio
        import adafruit_bme280

        addr = int(addr_str, 16)
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=addr)

        return {
            "temperature_c": round(sensor.temperature, 2),
            "humidity_percent": round(sensor.humidity, 2),
            "pressure_hpa": round(sensor.pressure, 2),
        }
    except Exception as e:
        print(f"⚠️ Error reading BME280: {e}", file=sys.stderr)
        return None


def main():
    setup_logging("read_i2c_sensors.log")
    config = dict(load_defaults(CONFIG_SECTION))
    bme280_addr = config.get("BME280_ADDR", "0")

    if bme280_addr == "1":
        addr = "0x76"
    elif bme280_addr == "2":
        addr = "0x77"
    elif bme280_addr == "3":
        addr = "0x76"  # default to 0x76 for 'both'
    else:
        addr = None

    now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    bme280_data = read_bme280(addr) if addr else None

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    conn.execute(
        """
        INSERT INTO sensor_readings (
            timestamp, temperature_c, humidity_percent, pressure_hpa,
            scd30_data, scd4x_data, ads1015_data, i2c_gps_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            now,
            bme280_data.get("temperature_c") if bme280_data else None,
            bme280_data.get("humidity_percent") if bme280_data else None,
            bme280_data.get("pressure_hpa") if bme280_data else None,
            None,  # SCD30 placeholder
            None,  # SCD4X placeholder
            None,  # ADS1015 placeholder
            None,  # GPS placeholder
        ),
    )

    conn.commit()
    conn.close()
    print(f"✅ Inserted reading for {now} into {DB_PATH}")


if __name__ == "__main__":
    main()
