#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import os
import time
import json
import logging
import psycopg
import shutil
import platform
from datetime import datetime

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Configuration
DB_PARAMS = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", 5432),
    "dbname": os.environ.get("POSTGRES_DB", "sensos"),
    "user": os.environ.get("POSTGRES_USER", "sensos"),
    "password": os.environ.get("POSTGRES_PASSWORD", "sensos"),
}
INTERVAL = int(os.environ.get("RECORD_INTERVAL_SEC", 300))
HOST_ROOT = os.environ.get("HOST_ROOT", "/host")


def host_path(*parts):
    return os.path.join(HOST_ROOT, *parts)


def connect_db():
    return psycopg.connect(**DB_PARAMS)


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sensos.system_stats (
                id SERIAL PRIMARY KEY,
                recorded_at TIMESTAMPTZ NOT NULL,
                hostname TEXT,
                uptime_seconds INTEGER,
                disk_available_gb REAL,
                memory_used_mb INTEGER,
                memory_total_mb INTEGER,
                load_1m REAL,
                load_5m REAL,
                load_15m REAL,
                sensor_stats JSONB
            )
            """
        )
        conn.commit()


def get_uptime():
    try:
        with open(host_path("proc", "uptime"), "r") as f:
            return int(float(f.readline().split()[0]))
    except:
        return None


def get_disk_free_gb(path="/sensos/data"):
    try:
        total, used, free = shutil.disk_usage(path)
        return round(free / (1024**3), 2)
    except:
        return None


def get_memory_usage():
    try:
        with open(host_path("proc", "meminfo"), "r") as f:
            lines = f.readlines()
            mem_total = (
                int(next(l for l in lines if "MemTotal" in l).split()[1]) // 1024
            )
            mem_free = (
                int(next(l for l in lines if "MemAvailable" in l).split()[1]) // 1024
            )
            mem_used = mem_total - mem_free
            return mem_used, mem_total
    except:
        return None, None


def get_load():
    try:
        with open(host_path("proc", "loadavg"), "r") as f:
            parts = f.readline().split()
            return float(parts[0]), float(parts[1]), float(parts[2])
    except:
        return None, None, None


def get_sensor_stats():
    return {
        "sound_recorder": {"clips_recorded": 0, "avg_sound_level": None},
        "sound_analyzer": {"clips_analyzed": 0, "errors": 0},
        "birdnet": {"detections": 0, "species": []},
    }


def insert_stats(conn, stats):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sensos.system_stats (
                recorded_at, hostname, uptime_seconds,
                disk_available_gb, memory_used_mb, memory_total_mb,
                load_1m, load_5m, load_15m, sensor_stats
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                datetime.utcnow(),
                platform.node(),
                stats["uptime_seconds"],
                stats["disk_available_gb"],
                stats["memory_used_mb"],
                stats["memory_total_mb"],
                stats["load_1m"],
                stats["load_5m"],
                stats["load_15m"],
                json.dumps(stats["sensor_stats"]),
            ),
        )
        conn.commit()


def main():
    conn = connect_db()
    create_schema(conn)

    while True:
        stats = {
            "uptime_seconds": get_uptime(),
            "disk_available_gb": get_disk_free_gb(),
            "memory_used_mb": None,
            "memory_total_mb": None,
            "load_1m": None,
            "load_5m": None,
            "load_15m": None,
            "sensor_stats": get_sensor_stats(),
        }

        mem_used, mem_total = get_memory_usage()
        stats["memory_used_mb"] = mem_used
        stats["memory_total_mb"] = mem_total

        load_1, load_5, load_15 = get_load()
        stats["load_1m"] = load_1
        stats["load_5m"] = load_5
        stats["load_15m"] = load_15

        logging.info(f"Recording system stats: {stats}")
        insert_stats(conn, stats)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
