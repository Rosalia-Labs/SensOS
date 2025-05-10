import os
import time
import logging
import sqlite3
import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("sqlite-importer")

SQLITE_PATH = "/microenv/i2c_readings.db"

DB_PARAMS = {
    "dbname": os.environ.get("POSTGRES_DB", "postgres"),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "sensos"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", 5432),
}


def connect_pg_with_retry():
    while True:
        try:
            conn = psycopg.connect(**DB_PARAMS, row_factory=dict_row)
            return conn
        except Exception as e:
            logger.warning(f"Waiting for PostgreSQL: {e}")
            time.sleep(5)


def create_schema_if_missing(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS sensos;
            CREATE TABLE IF NOT EXISTS sensos.i2c_readings (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL,
                device_address TEXT NOT NULL,
                sensor_type TEXT NOT NULL,
                key TEXT NOT NULL,
                value DOUBLE PRECISION
            );
            CREATE INDEX IF NOT EXISTS idx_i2c_time ON sensos.i2c_readings (timestamp);
            """
        )
        conn.commit()
        logger.info("Ensured sensos.i2c_readings table exists.")


def main():
    time.sleep(60)
    pg_conn = connect_pg_with_retry()
    create_schema_if_missing(pg_conn)

    while True:
        try:
            with sqlite3.connect(SQLITE_PATH) as sqlite_conn:
                sqlite_conn.row_factory = sqlite3.Row
                sqlite_cur = sqlite_conn.cursor()

                rows = sqlite_cur.execute(
                    "SELECT * FROM i2c_readings ORDER BY id LIMIT 10"
                ).fetchall()

                if not rows:
                    logger.info("No rows to import.")
                    time.sleep(60)
                    continue

                for row in rows:
                    try:
                        with pg_conn.cursor() as pg_cur:
                            pg_cur.execute(
                                """
                                INSERT INTO sensos.i2c_readings
                                (timestamp, device_address, sensor_type, key, value)
                                VALUES (%s, %s, %s, %s, %s)
                                """,
                                (
                                    row["timestamp"],
                                    row["device_address"],
                                    row["sensor_type"],
                                    row["key"],
                                    row["value"],
                                ),
                            )

                        sqlite_cur.execute(
                            "DELETE FROM i2c_readings WHERE id = ?", (row["id"],)
                        )

                        logger.info(f"Imported and deleted row {row['id']}")

                    except Exception as e:
                        logger.error(f"Error syncing row {row['id']}: {e}")
                        pg_conn.rollback()
                        sqlite_conn.rollback()
                        break  # Stop batch on error

                pg_conn.commit()
                sqlite_conn.commit()

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            pg_conn = connect_pg_with_retry()

        time.sleep(1)


if __name__ == "__main__":
    main()
