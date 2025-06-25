import os
import requests
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timedelta
from decimal import Decimal
import platform

NETWORK_CONF = "/sensos/etc/network.conf"
API_PASS_FILE = "/sensos/keys/api_password"


def load_network_conf():
    config = {}
    if os.path.isfile(NETWORK_CONF):
        with open(NETWORK_CONF) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    config[k] = v
    return config


def load_api_password():
    if os.path.isfile(API_PASS_FILE):
        with open(API_PASS_FILE) as f:
            return f.read().strip()
    return None


def convert_decimals(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_decimals(v) for v in obj]
    return obj


def get_api_vars():
    config = load_network_conf()
    server_ip = os.environ.get("SERVER_WG_IP", config.get("SERVER_WG_IP"))
    server_port = os.environ.get("SERVER_PORT", config.get("SERVER_PORT", "8000"))
    api_user = os.environ.get("API_USER", "mydevice")
    api_pass = os.environ.get("API_PASS") or load_api_password() or "secret"
    api_path = os.environ.get("API_PATH", "/api/client-status")
    api_url = os.environ.get("API_URL") or (
        f"http://{server_ip}:{server_port}{api_path}"
        if server_ip and server_port
        else None
    )
    return api_url, api_user, api_pass


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
    api_url, api_user, api_pass = get_api_vars()
    if not api_url or not api_pass:
        print(
            "API URL or password missing. Check /sensos/etc/network.conf and /sensos/keys/api_password."
        )
        return

    with psycopg.connect(**DB_PARAMS, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            stats = summarize_stats(cur)

    if not stats or stats["count"] == 0:
        print("No system stats found to report for last 24 hours.")
        return

    payload = {
        "client_id": os.environ.get("CLIENT_ID", platform.node()),
        "uptime": stats["max_uptime"],
        "cpu_usage": stats["avg_load_1m"],
        "memory_usage": stats["avg_mem"],
        "disk_usage": stats["min_disk"],
        "version": os.environ.get("SOFTWARE_VERSION", "v0.1"),
        "error_count": 0,
        "latency": None,
        "ip_address": os.environ.get("IP_ADDRESS", None),
        "temperature": None,
        "battery_level": None,
        "status_message": f"Reporting {stats['count']} system samples in past 24h",
    }

    payload = convert_decimals(payload)
    print(f"Posting payload: {payload}")
    response = requests.post(
        api_url, auth=(api_user, api_pass), json=payload, timeout=10
    )
    print("Server responded:", response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
