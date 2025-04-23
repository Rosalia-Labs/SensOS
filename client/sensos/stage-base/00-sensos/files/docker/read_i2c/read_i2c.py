import os
import time
import board
import busio
import psycopg
import logging

import adafruit_scd30
import adafruit_scd4x
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_bme280 import Adafruit_BME280_I2C

# Optional: set up basic logging to stdout
logging.basicConfig(level=logging.INFO)

# PostgreSQL connection
conn = psycopg.connect(
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    host=os.environ["DB_HOST"],
    port=os.environ["DB_PORT"],
)
conn.execute("CREATE SCHEMA IF NOT EXISTS sensos")
conn.execute(
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

# Initialize I2C
i2c = busio.I2C(board.SCL, board.SDA)
while not i2c.try_lock():
    pass
detected_addresses = i2c.scan()
i2c.unlock()

# Sensor init
sensors = []

# BME280
for addr in detected_addresses:
    try:
        sensor = Adafruit_BME280_I2C(i2c, address=addr)
        _ = sensor.temperature
        sensors.append((hex(addr), sensor, f"bme280:{hex(addr)}"))
        logging.info(f"Initialized BME280 at address {hex(addr)}")
    except Exception:
        pass

# SCD30 (0x61)
try:
    scd30 = adafruit_scd30.SCD30(i2c)
    if scd30.data_available:
        _ = scd30.CO2
        sensors.append(("0x61", scd30, "scd30"))
        logging.info("Initialized SCD30 CO₂ sensor at address 0x61")
except Exception as e:
    logging.warning(f"Could not initialize SCD30: {e}")

# Vegetronix via ADS1015 (assumed at 0x48)
try:
    ads = ADS.ADS1015(i2c, data_rate=128)
    ads.gain = 1
    for ch_name, ch_enum in {
        "A0": ADS.P0,
        "A1": ADS.P1,
        "A2": ADS.P2,
        "A3": ADS.P3,
    }.items():
        try:
            analog_in = AnalogIn(ads, ch_enum)
            sensors.append(("0x48", analog_in, f"vegetronix:{ch_name}"))
            logging.info(f"Initialized Vegetronix sensor on {ch_name}")
        except Exception as e:
            logging.warning(f"Failed to init Vegetronix channel {ch_name}: {e}")
except Exception as e:
    logging.warning(f"Could not initialize ADS1015 (Vegetronix): {e}")

# SCD4x (SCD40/SCD41) – typically at 0x62
try:
    scd4x = adafruit_scd4x.SCD4X(i2c)
    scd4x.start_periodic_measurement()
    time.sleep(1)  # wait for first measurement
    if scd4x.data_ready:
        _ = scd4x.CO2
        sensors.append(("0x62", scd4x, "scd4x"))
        logging.info("Initialized SCD4x CO₂ sensor at address 0x62")
except Exception as e:
    logging.warning(f"Could not initialize SCD4x: {e}")

if not sensors:
    logging.error("No sensors initialized. Exiting.")
    exit(1)

# Read loop
while True:
    for addr, sensor, sensor_type in sensors:
        readings = {}

        if sensor_type.startswith("bme280"):
            readings = {
                "temperature": sensor.temperature,
                "humidity": sensor.humidity,
                "pressure": sensor.pressure,
            }

        elif sensor_type == "scd30":
            if sensor.data_available:
                readings = {
                    "temperature": sensor.temperature,
                    "humidity": sensor.relative_humidity,
                    "co2": sensor.CO2,
                }

        elif sensor_type == "scd4x":
            if sensor.data_ready:
                readings = {
                    "temperature": sensor.temperature,
                    "humidity": sensor.relative_humidity,
                    "co2": sensor.CO2,
                }
            else:
                continue  # skip if no new data

        elif sensor_type.startswith("vegetronix:"):
            channel = sensor_type.split(":")[1]
            try:
                voltage = sensor.voltage
                readings = {channel: voltage}
            except Exception as e:
                logging.warning(f"Error reading Vegetronix channel {channel}: {e}")
                continue

        else:
            continue  # Unknown sensor

        try:
            for key, value in readings.items():
                conn.execute(
                    "INSERT INTO sensos.i2c_readings (...) VALUES (%s, %s, %s, %s)",
                    (addr, sensor_type, key, value),
                )
        except Exception as e:
            logging.error(f"DB insert failed for {sensor_type} at {addr}: {e}")

        logging.info(f"Recorded data from {addr} ({sensor_type}): {readings}")
    conn.commit()
    time.sleep(60)
