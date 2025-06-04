import os
import requests
import psycopg
from psycopg.rows import dict_row

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


def get_latest_stats():
    with psycopg.connect(**DB_PARAMS, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM sensos.system_stats
                ORDER BY recorded_at DESC
                LIMIT 1;
                """
            )
            return cur.fetchone()


def main():
    stats = get_latest_stats()
    if not stats:
        print("No system stats found to report.")
        return

    payload = {
        "client_id": os.environ.get("CLIENT_ID", stats["hostname"]),
        "uptime": stats["uptime_seconds"],
        "cpu_usage": stats.get("load_1m"),  # Or adjust to your schema
        "memory_usage": stats.get("memory_used_mb"),
        "disk_usage": stats.get("disk_available_gb"),
        "version": os.environ.get("SOFTWARE_VERSION", "v0.1"),
        "error_count": 0,  # Adjust as needed
        "latency": None,  # Not tracked? Omit or set to None
        "ip_address": os.environ.get("IP_ADDRESS", None),
        "temperature": None,  # Or pull from sensors if available
        "battery_level": None,  # Or pull from sensors if available
        "status_message": "OK",
    }

    print(f"Posting payload: {payload}")
    response = requests.post(
        API_URL, auth=(API_USER, API_PASS), json=payload, timeout=10
    )
    print("Server responded:", response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
