#!/usr/bin/env python3
import os
import sys
import json
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


def read_bme280(addr_str: str):
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


def read_ads1015():
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


def read_scd30():
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


def read_scd4x():
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


def read_i2c_gps():
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


def main():
    setup_logging("read_i2c_sensors.log")

    now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    readings = []

    # BME280
    bme280_addr = config.get("BME280_ADDR", "0")
    if bme280_addr == "1":
        addrs = ["0x76"]
    elif bme280_addr == "2":
        addrs = ["0x77"]
    elif bme280_addr == "3":
        addrs = ["0x76", "0x77"]
    else:
        addrs = []

    for addr in addrs:
        bme280_data = read_bme280(addr)
        print(f"🌡️  BME280 ({addr}) data: {bme280_data}")
        if bme280_data:
            readings += flatten_sensor_data(bme280_data, addr, "BME280", now)

    # ADS1015
    ads1015_data = (
        read_ads1015() if config.get("ADS1015", "").lower() == "true" else None
    )
    print(f"📈 ADS1015 data: {ads1015_data}")
    readings += (
        flatten_sensor_data(ads1015_data, "0x48", "ADS1015", now)
        if ads1015_data
        else []
    )

    # SCD30
    scd30_data = read_scd30() if config.get("SCD30", "").lower() == "true" else None
    print(f"🫁 SCD30 data: {scd30_data}")
    readings += (
        flatten_sensor_data(scd30_data, "0x61", "SCD30", now) if scd30_data else []
    )

    # SCD4X
    scd4x_data = read_scd4x() if config.get("SCD4X", "").lower() == "true" else None
    print(f"🫁 SCD4X data: {scd4x_data}")
    readings += (
        flatten_sensor_data(scd4x_data, "0x62", "SCD4X", now) if scd4x_data else []
    )

    # GPS
    gps_data = read_i2c_gps() if config.get("I2C_GPS", "").lower() == "true" else None
    print(f"📡 GPS data: {gps_data}")
    readings += (
        flatten_sensor_data(gps_data, "0x10", "I2C_GPS", now) if gps_data else []
    )

    print(f"📝 Prepared {len(readings)} readings to insert")

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
    print(f"✅ Inserted {len(readings)} rows for {now} into {DB_PATH}")


if __name__ == "__main__":
    main()
