import os
import time
import board
import busio
import psycopg
import logging
import sys
from psycopg.rows import dict_row

import adafruit_scd30
import adafruit_scd4x
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_bme280 import Adafruit_BME280_I2C

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("sensor-poller")

# Constants
POLL_INTERVAL = 60
REDISCOVERY_INTERVAL = 600  # 10 minutes

DB_PARAMS = {
    "dbname": os.environ.get("POSTGRES_DB"),
    "user": os.environ.get("POSTGRES_USER"),
    "password": os.environ.get("POSTGRES_PASSWORD"),
    "host": os.environ.get("DB_HOST"),
    "port": os.environ.get("DB_PORT"),
}


def connect_with_retry():
    while True:
        try:
            conn = psycopg.connect(**DB_PARAMS, row_factory=dict_row)
            logger.info("Connected to database.")
            return conn
        except Exception as e:
            logger.warning(f"Database connection failed: {e}")
            time.sleep(5)


def setup_schema(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS sensos")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sensos.i2c_readings (
                    id SERIAL PRIMARY KEY,
                    device_address TEXT NOT NULL,
                    sensor_type TEXT,
                    key TEXT NOT NULL,
                    value DOUBLE PRECISION,
                    timestamp TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to create schema or table: {e}")
        conn.rollback()


def safe_i2c_scan(i2c):
    try:
        while not i2c.try_lock():
            time.sleep(0.1)
        addresses = i2c.scan()
        return addresses
    except Exception as e:
        logger.error(f"I2C scan failed: {e}")
        return []
    finally:
        try:
            i2c.unlock()
        except Exception:
            pass


def rediscover_sensors(i2c):
    sensors = []

    # BME280
    bme280_addrs = os.environ.get("BME280_ADDRS", "")
    for addr_str in bme280_addrs.split(","):
        addr_str = addr_str.strip()
        if not addr_str:
            continue
        try:
            addr = int(addr_str, 16)
            sensor = Adafruit_BME280_I2C(i2c, address=addr)
            _ = sensor.temperature
            sensors.append((addr_str, sensor, f"bme280:{addr_str}"))
            logger.info(f"Initialized BME280 at {addr_str}")
        except Exception as e:
            logger.warning(f"BME280 init failed at {addr_str}: {e}")

    # SCD30
    if os.environ.get("SCD30", "").lower() == "true":
        try:
            scd30 = adafruit_scd30.SCD30(i2c)
            if scd30.data_available:
                _ = scd30.CO2
                sensors.append(("0x61", scd30, "scd30"))
                logger.info("Initialized SCD30")
        except Exception as e:
            logger.warning(f"SCD30 init failed: {e}")

    # ADS1015 and Vegetronix
    if os.environ.get("ADS1015", "").lower() == "true":
        try:
            ads = ADS.ADS1015(i2c, data_rate=128)
            ads.gain = 1
            channels = os.environ.get("VEGETRONIX_CHANNELS", "").split(",")
            channel_map = {"A0": ADS.P0, "A1": ADS.P1, "A2": ADS.P2, "A3": ADS.P3}
            for ch in channels:
                ch = ch.strip()
                if ch in channel_map:
                    try:
                        analog_in = AnalogIn(ads, channel_map[ch])
                        sensors.append(("0x48", analog_in, f"vegetronix:{ch}"))
                        logger.info(f"Initialized Vegetronix on {ch}")
                    except Exception as e:
                        logger.warning(f"Vegetronix {ch} init failed: {e}")
        except Exception as e:
            logger.warning(f"ADS1015 init failed: {e}")

    # SCD4x
    if os.environ.get("SCD4X", "").lower() == "true":
        try:
            scd4x = adafruit_scd4x.SCD4X(i2c)
            scd4x.start_periodic_measurement()
            time.sleep(1)
            if scd4x.data_ready:
                _ = scd4x.CO2
                sensors.append(("0x62", scd4x, "scd4x"))
                logger.info("Initialized SCD4x")
        except Exception as e:
            logger.warning(f"SCD4x init failed: {e}")

    if not sensors:
        logger.warning("No sensors initialized. Check env vars.")
    return sensors


def record_readings(addr, sensor, sensor_type, conn):
    readings = {}
    try:
        if sensor_type.startswith("bme280"):
            readings = {
                "temperature": sensor.temperature,
                "humidity": sensor.humidity,
                "pressure": sensor.pressure,
            }
        elif sensor_type == "scd30" and sensor.data_available:
            readings = {
                "temperature": sensor.temperature,
                "humidity": sensor.relative_humidity,
                "co2": sensor.CO2,
            }
        elif sensor_type == "scd4x" and sensor.data_ready:
            readings = {
                "temperature": sensor.temperature,
                "humidity": sensor.relative_humidity,
                "co2": sensor.CO2,
            }
        elif sensor_type.startswith("vegetronix:"):
            channel = sensor_type.split(":")[1]
            readings = {channel: sensor.voltage}
    except Exception as e:
        logger.warning(f"Sensor read failed for {sensor_type} at {addr}: {e}")
        return

    try:
        with conn.cursor() as cur:
            for key, value in readings.items():
                cur.execute(
                    """
                    INSERT INTO sensos.i2c_readings (device_address, sensor_type, key, value)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (addr, sensor_type, key, value),
                )
        conn.commit()
        logger.info(f"Recorded {len(readings)} readings from {addr} ({sensor_type})")
    except Exception as e:
        logger.error(f"DB insert failed for {sensor_type} at {addr}: {e}")
        conn.rollback()


def main():
    conn = connect_with_retry()
    setup_schema(conn)

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
    except Exception as e:
        logger.error(f"Failed to initialize I2C: {e}")
        sys.exit(1)

    sensors = []
    last_discovery = 0

    try:
        while True:
            now = time.time()
            if not sensors or now - last_discovery > REDISCOVERY_INTERVAL:
                logger.info("Running sensor rediscovery...")
                sensors = rediscover_sensors(i2c)
                last_discovery = now

            if sensors:
                for addr, sensor, sensor_type in sensors:
                    record_readings(addr, sensor, sensor_type, conn)
            else:
                logger.info("No sensors currently available.")

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopping sensor poller.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
