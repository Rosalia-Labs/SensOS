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
DB_PATH = Path("/sensos/data/microenv/sensor_readings.db")


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


def get_interval(key: str) -> int | None:
    val_str = config.get(key, "").strip()

    # Per-sensor value takes priority
    if val_str:
        try:
            val = int(val_str)
            return val if val > 0 else None
        except ValueError:
            return None

    # No per-sensor setting; check INTERVAL_SEC fallback
    fallback_str = config.get("INTERVAL_SEC", "").strip()
    if fallback_str:
        try:
            val = int(fallback_str)
            return val if val > 0 else None
        except ValueError:
            return None

    # Neither per-sensor nor global fallback present
    return None


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

        i2c = busio.I2C(board.SCL, board.SDA)
        scd = adafruit_scd4x.SCD4X(i2c)
        scd.start_periodic_measurement()
        time.sleep(5)

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
    try:
        import smbus2
        import pynmea2

        I2C_ADDR = int(addr_str, 16)
        bus = smbus2.SMBus(1)

        available = bus.read_byte_data(I2C_ADDR, 0xFD)
        if available == 0:
            return {"fix": 0}

        raw = []
        for _ in range(available):
            raw.append(chr(bus.read_byte_data(I2C_ADDR, 0xFF)))
        nmea = "".join(raw)

        for line in nmea.splitlines():
            if line.startswith("$GPGGA") or line.startswith("$GPRMC"):
                try:
                    msg = pynmea2.parse(line)

                    fix_quality = getattr(msg, "gps_qual", None)
                    fix = (
                        int(fix_quality) if fix_quality and fix_quality.isdigit() else 0
                    )

                    if fix == 0:
                        return {"fix": 0}

                    return {
                        "latitude": getattr(msg, "latitude", None),
                        "longitude": getattr(msg, "longitude", None),
                        "altitude": getattr(msg, "altitude", None),
                        "timestamp": (
                            msg.timestamp.isoformat()
                            if hasattr(msg, "timestamp")
                            else None
                        ),
                        "fix": fix,
                    }
                except pynmea2.ParseError:
                    continue
        return {"fix": 0}
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


def store_readings(readings):
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            ensure_schema(conn)
            conn.executemany(
                """
                INSERT INTO i2c_readings (timestamp, device_address, sensor_type, key, value)
                VALUES (?, ?, ?, ?, ?)
                """,
                readings,
            )
            conn.commit()
        print(f"✅ Stored {len(readings)} readings.")
    except Exception as e:
        print(f"❌ Failed to store readings: {e}", file=sys.stderr)


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

    last_run = {}
    interval_sec = {}

    for key, *_ in sensors:
        interval = get_interval(f"{key}_INTERVAL_SEC")
        if interval is not None:
            interval_sec[key] = interval
            last_run[key] = 0

    if not interval_sec:
        print("❌ No sensors enabled. Exiting.")
        sys.exit(1)

    print("🔁 Entering sensor loop")
    while True:
        now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        current_time = time.time()
        readings = []

        for key, addr, sensor_type, read_func in sensors:
            interval = interval_sec.get(key)
            if interval is None:
                continue
            if current_time - last_run[key] >= interval:
                print(f"⏱️ Polling {sensor_type} at {addr}")
                try:
                    data = read_func(addr)
                except Exception as e:
                    print(f"⚠️ Error polling {sensor_type}: {e}", file=sys.stderr)
                    data = None
                print(f"📟 {sensor_type} ({addr}) data: {data}")
                readings += (
                    flatten_sensor_data(data, addr, sensor_type, now) if data else []
                )
                last_run[key] = current_time

        if readings:
            print(f"📝 Inserting {len(readings)} readings at {now}")
            store_readings(readings)

        next_due_in = min(
            max(0.5, interval - (current_time - last_run[key]))
            for key, interval in interval_sec.items()
        )
        time.sleep(min(5, math.ceil(next_due_in)))


if __name__ == "__main__":
    main()
