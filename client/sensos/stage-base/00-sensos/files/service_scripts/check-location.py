#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import os
import time
import json
import math
import requests
import subprocess
import sys

sys.path.insert(0, "/sensos/lib")
from utils import *

CONFIG_PATH = "/sensos/etc/location.conf"
SET_PEER_URL = "/set-peer-location"
DEFAULT_SERVER = "localhost"
DEFAULT_PORT = 8000
GPS_SAMPLES = 300  # 5 minutes assuming 1Hz
DISTANCE_THRESHOLD_METERS = 100
SLEEP_INTERVAL = 3600  # seconds (1 hour)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def load_location():
    config = read_kv_config(CONFIG_PATH)
    try:
        lat = config.get("LATITUDE")
        lon = config.get("LONGITUDE")
        if lat is None or lon is None:
            return None, None
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def write_location(lat, lon):
    create_dir("/sensos/etc", owner="sensos-admin", mode=0o755)
    write_file(
        CONFIG_PATH,
        f"LATITUDE={lat:.6f}\nLONGITUDE={lon:.6f}\n",
        mode=0o644,
        user="sensos-admin",
    )


def post_location(lat, lon, config_server, port, wg_ip):
    url = f"http://{config_server}:{port}{SET_PEER_URL}"
    password = read_api_password()
    if not password:
        print("No API password; skipping POST")
        return
    auth_header = {
        "Authorization": f"Basic {requests.auth._basic_auth_str('', password)}"
    }
    data = {"wg_ip": wg_ip, "latitude": lat, "longitude": lon}
    try:
        r = requests.post(url, json=data, headers=auth_header)
        print(f"POST to {url} status: {r.status_code}")
    except Exception as e:
        print(f"Failed to POST location: {e}")


def read_gps_sample():
    try:
        line = subprocess.check_output(
            "gpspipe -w -n 10", shell=True, text=True
        ).splitlines()
        for l in line:
            if "TPV" in l:
                data = json.loads(l)
                if data.get("class") == "TPV" and "lat" in data and "lon" in data:
                    return data["lat"], data["lon"]
    except Exception as e:
        print(f"Error reading GPS: {e}")
    return None, None


def collect_gps_average(seconds):
    lat_sum = 0
    lon_sum = 0
    count = 0
    end = time.time() + seconds
    while time.time() < end:
        lat, lon = read_gps_sample()
        if lat and lon:
            lat_sum += lat
            lon_sum += lon
            count += 1
        time.sleep(1)
    if count == 0:
        return None, None
    return lat_sum / count, lon_sum / count


def main():
    network_conf = read_network_conf()
    config_server = os.getenv(
        "SENSOS_CONFIG_SERVER", network_conf.get("SERVER_WG_IP", DEFAULT_SERVER)
    )
    port = int(os.getenv("SENSOS_CONFIG_PORT", network_conf.get("SERVER_PORT", DEFAULT_PORT)))
    wg_ip = network_conf.get("CLIENT_WG_IP")
    if not wg_ip:
        print("CLIENT_WG_IP missing from /sensos/etc/network.conf", file=sys.stderr)
        return

    while True:
        existing_lat, existing_lon = load_location()
        print(f"Existing location: {existing_lat}, {existing_lon}")

        # Try a quick sample
        current_lat, current_lon = read_gps_sample()
        if not current_lat:
            print("No GPS data available. Sleeping.")
            time.sleep(SLEEP_INTERVAL)
            continue

        if existing_lat and existing_lon:
            dist = haversine(existing_lat, existing_lon, current_lat, current_lon)
            print(f"Distance from saved location: {dist:.1f} m")
            if dist < DISTANCE_THRESHOLD_METERS:
                print("Location is within threshold; nothing to update.")
                time.sleep(SLEEP_INTERVAL)
                continue

        print("Collecting GPS samples to compute average...")
        avg_lat, avg_lon = collect_gps_average(300)
        if not avg_lat:
            print("Could not average GPS. Sleeping.")
            time.sleep(SLEEP_INTERVAL)
            continue

        if existing_lat and existing_lon:
            dist = haversine(existing_lat, existing_lon, avg_lat, avg_lon)
            if dist < DISTANCE_THRESHOLD_METERS:
                print("Average still within threshold. No update needed.")
                time.sleep(SLEEP_INTERVAL)
                continue

        print(f"Saving new location: {avg_lat:.6f}, {avg_lon:.6f}")
        write_location(avg_lat, avg_lon)
        post_location(avg_lat, avg_lon, config_server, port, wg_ip)
        time.sleep(SLEEP_INTERVAL)


if __name__ == "__main__":
    main()
