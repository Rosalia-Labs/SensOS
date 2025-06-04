import os
import requests
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timedelta
import platform

API_URL = os.environ.get("API_URL", "https://your.server/api/client-status")
API_USER = os.environ.get("API_USER", "mydevice")
API_PASS = os.environ.get("API_PASS", "secret")

DB_PARAMS = {
    "dbname": os.environ.get("POSTGRES_DB", "sensos"),
    "user": os.environ.get("POSTGRES_USER", "sensos"),
    "password": os.environ.get("POSTGRES_PASSWORD", "sensos"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", 5432),
}


def summarize_stats(cur):
    cutoff = datetime.utcnow() - timedelta(days=1)
    cur.execute(
        """
        SELECT
            COUNT(*) AS count,
            MAX(uptime_seconds) AS max_uptime,
            AVG(disk_available_gb) AS avg_disk,
            MIN(disk_available_gb) AS min_disk,
            AVG(memory_used_mb) AS avg_mem,
            MAX(memory_used_mb) AS max_mem,
            MIN(memory_used_mb) AS min_mem,
            AVG(memory_total_mb) AS avg_mem_total,
            AVG(load_1m) AS avg_load_1m,
            MAX(load_1m) AS max_load_1m,
            MIN(load_1m) AS min_load_1m
        FROM sensos.system_stats
        WHERE recorded_at >= %s
    """,
        (cutoff,),
    )
    return cur.fetchone()


def main():
    with psycopg.connect(**DB_PARAMS, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            stats = summarize_stats(cur)

    if not stats or stats["count"] == 0:
        print("No system stats found to report for last 24 hours.")
        return

    payload = {
        "client_id": os.environ.get("CLIENT_ID", platform.node()),
        "uptime": stats["max_uptime"],  # Uptime is best as max
        "cpu_usage": stats["avg_load_1m"],
        "memory_usage": stats["avg_mem"],
        "disk_usage": stats["min_disk"],  # Min free disk (worst)
        "version": os.environ.get("SOFTWARE_VERSION", "v0.1"),
        "error_count": 0,  # Adjust if you track errors
        "latency": None,
        "ip_address": os.environ.get("IP_ADDRESS", None),
        "temperature": None,
        "battery_level": None,
        "status_message": f"Reporting {stats['count']} system samples in past 24h",
    }

    print(f"Posting payload: {payload}")
    response = requests.post(
        API_URL, auth=(API_USER, API_PASS), json=payload, timeout=10
    )
    print("Server responded:", response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
