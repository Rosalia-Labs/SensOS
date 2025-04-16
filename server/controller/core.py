# core.py
import os
import stat
import subprocess
import ipaddress
import platform
import logging
import psycopg
import socket
import docker
import time
import json
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg.errors import UniqueViolation
from typing import Tuple, Optional
from pydantic import BaseModel, IPvAnyAddress
from datetime import datetime, timedelta
from pathlib import Path

# ------------------------------------------------------------
# Logging & Configuration
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WG_CONFIG_DIR = Path("/config/wg_confs")
CONTROLLER_CONFIG_DIR = Path("/etc/wireguard")

POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
if not POSTGRES_PASSWORD:
    raise ValueError("POSTGRES_PASSWORD is not set. Exiting.")
DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@sensos-database/postgres"

API_PASSWORD = os.getenv("API_PASSWORD")
if not API_PASSWORD:
    raise ValueError("API_PASSWORD is not set. Exiting.")

VERSION_MAJOR = os.getenv("VERSION_MAJOR", "Unknown")
VERSION_MINOR = os.getenv("VERSION_MINOR", "Unknown")
VERSION_PATCH = os.getenv("VERSION_PATCH", "Unknown")
VERSION_SUFFIX = os.getenv("VERSION_SUFFIX", "")
GIT_COMMIT = os.getenv("GIT_COMMIT", "Unknown")
GIT_BRANCH = os.getenv("GIT_BRANCH", "Unknown")
GIT_TAG = os.getenv("GIT_TAG", "Unknown")
GIT_DIRTY = os.getenv("GIT_DIRTY", "false")
EXPOSE_CONTAINERS = os.getenv("EXPOSE_CONTAINERS", "false").lower() == "true"

# ------------------------------------------------------------
# Application Lifespan
# ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Called lifespan async context manager...")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                logger.info("Creating schema 'sensos' if not exists...")
                cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
                cur.execute("SET search_path TO sensos, public;")
                create_version_history_table(cur)
                update_version_history_table(cur)
                create_networks_table(cur)
                create_wireguard_peers_table(cur)
                create_wireguard_keys_table(cur)
                create_ssh_keys_table(cur)
                create_client_status_table(cur)
                create_hardware_profile_table(cur)
                create_peer_location_table(cur)
                network_id = create_initial_network(cur)
                if network_id:
                    add_peers_to_wireguard()
                    restart_container("sensos-wireguard")
                    start_controller_wireguard()
                    logger.info("✅ WireGuard setup completed.")
        logger.info("✅ Database schema and tables initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}", exc_info=True)
    yield
    logger.info("Shutting down!")

# ------------------------------------------------------------
# Security & Authentication
# ------------------------------------------------------------
security = HTTPBasic()

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials

# ------------------------------------------------------------
# Database Connection
# ------------------------------------------------------------
def get_db(retries=10, delay=3):
    for attempt in range(retries):
        try:
            return psycopg.connect(DATABASE_URL, autocommit=True)
        except psycopg.OperationalError:
            if attempt == retries - 1:
                raise
            logger.info(f"Database not ready, retrying in {delay} seconds... (Attempt {attempt+1}/{retries})")
            time.sleep(delay)

# ------------------------------------------------------------
# Core Utility Functions
# ------------------------------------------------------------

def resolve_hostname(value):
    try:
        socket.inet_pton(socket.AF_INET, value)
        return value
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, value)
            return value
        except OSError:
            pass
    try:
        addr_info = socket.getaddrinfo(value, None, family=socket.AF_UNSPEC)
        for family, _, _, _, sockaddr in addr_info:
            if family in (socket.AF_INET, socket.AF_INET6):
                return sockaddr[0]
    except socket.gaierror:
        pass
    return None

def generate_default_ip_range(name):
    hash_val = sum(ord(c) for c in name) % 256
    return f"10.{hash_val}.0.0/16"

def generate_wireguard_keys():
    private_key = subprocess.run("wg genkey", shell=True, capture_output=True, text=True).stdout.strip()
    public_key = subprocess.run(f"echo {private_key} | wg pubkey", shell=True, capture_output=True, text=True).stdout.strip()
    return private_key, public_key

def insert_peer(network_id: int, wg_ip: str, note: Optional[str] = None) -> Tuple[int, str]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sensos.wireguard_peers (network_id, wg_ip, note)
                VALUES (%s, %s, %s)
                RETURNING id, uuid;
                """,
                (network_id, wg_ip, note),
            )
            return cur.fetchone()

def register_wireguard_key_in_db(wg_ip: str, wg_public_key: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM sensos.wireguard_peers WHERE wg_ip = %s;", (wg_ip,))
            peer = cur.fetchone()
            if not peer:
                return None
            peer_id = peer[0]
            cur.execute(
                "INSERT INTO sensos.wireguard_keys (peer_id, wg_public_key) VALUES (%s, %s);",
                (peer_id, wg_public_key),
            )
    return {"wg_ip": wg_ip, "wg_public_key": wg_public_key}

def create_network_entry(cur, name, wg_public_ip=None, wg_port=None):
    wg_public_ip = wg_public_ip or resolve_hostname(os.getenv("WG_SERVER_IP", "127.0.0.1"))
    wg_port = wg_port or int(os.getenv("WG_PORT", "51820"))
    ip_range = generate_default_ip_range(name)
    private_key, public_key = generate_wireguard_keys()
    try:
        cur.execute(
            """
            INSERT INTO sensos.networks (name, ip_range, wg_public_ip, wg_port, wg_public_key)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (name, ip_range, wg_public_ip, wg_port, public_key),
        )
        network_id = cur.fetchone()[0]
        logger.info(f"✅ Network '{name}' created with ID: {network_id}")
        create_wireguard_configs(network_id, name, ip_range, private_key, public_key)
        add_peers_to_wireguard()
        return {
            "id": network_id,
            "name": name,
            "ip_range": ip_range,
            "wg_public_ip": wg_public_ip,
            "wg_port": wg_port,
            "wg_public_key": public_key,
        }
    except psycopg.errors.UniqueViolation as e:
        logger.warning(f"⚠️ Unique constraint violated when creating network '{name}': {e}")
        if "networks_name_key" in str(e):
            cur.execute("SELECT id, ip_range, wg_public_ip, wg_port, wg_public_key FROM sensos.networks WHERE name = %s;", (name,))
        elif "networks_ip_range_key" in str(e):
            cur.execute("SELECT id, name, wg_public_ip, wg_port, wg_public_key FROM sensos.networks WHERE ip_range = %s;", (ip_range,))
        elif "networks_wg_public_key_key" in str(e):
            cur.execute("SELECT id, name, ip_range, wg_public_ip, wg_port FROM sensos.networks WHERE wg_public_key = %s;", (public_key,))
        elif "networks_wg_public_ip_wg_port_key" in str(e):
            cur.execute("SELECT id, name, ip_range, wg_public_key FROM sensos.networks WHERE wg_public_ip = %s AND wg_port = %s;", (wg_public_ip, wg_port))
        else:
            raise RuntimeError("Constraint violation but failed to retrieve existing network.")
        existing_network = cur.fetchone()
        if not existing_network:
            raise RuntimeError("Constraint violation but failed to retrieve existing network.")
        result = {
            "id": existing_network[0],
            "name": existing_network[1] if len(existing_network) > 1 else name,
            "ip_range": existing_network[2] if len(existing_network) > 2 else ip_range,
            "wg_public_ip": existing_network[3] if len(existing_network) > 3 else wg_public_ip,
            "wg_port": existing_network[4] if len(existing_network) > 4 else wg_port,
            "wg_public_key": existing_network[5] if len(existing_network) > 5 else public_key,
        }
        if result["wg_public_key"] != public_key:
            raise RuntimeError(
                f"❌ Refusing to overwrite existing WireGuard key for network '{name}'. "
                "Restore the original key or delete the network first."
            )
        logger.info(f"✅ Returning existing network: {result}")
        return result

def create_wireguard_configs(network_id: int, name: str, ip_range: str, private_key: str, wg_public_key: str):
    WG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    wg_config_path = WG_CONFIG_DIR / f"{name}.conf"
    controller_config_path = CONTROLLER_CONFIG_DIR / f"{name}.conf"
    base_ip = ip_range.split("/")[0]
    network_prefix = ".".join(base_ip.split(".")[:3])
    # API Proxy always assigned prefix .1
    api_proxy_ip = f"{network_prefix}.1"
    insert_peer(network_id, api_proxy_ip, "API server")
    wg_interface_ip = ""
    wg_peers = []
    if EXPOSE_CONTAINERS:
        wg_interface_ip = f"{network_prefix}.2/16"
        controller_ip = f"{network_prefix}.3/16"
        controller_private_key, controller_public_key = generate_wireguard_keys()
        insert_peer(network_id, controller_ip.split("/")[0])
        register_wireguard_key_in_db(controller_ip.split("/")[0], controller_public_key)
        wg_peers.append(f"\n[Peer]\nPublicKey = {controller_public_key}\nAllowedIPs = {controller_ip.split('/')[0]}/32\n")
        wireguard_container_ip = get_container_ip("sensos-wireguard")
        controller_config_content = f"""[Interface]
Address = {controller_ip}
PrivateKey = {controller_private_key}

[Peer]
PublicKey = {wg_public_key}
AllowedIPs = {ip_range}
Endpoint = {wireguard_container_ip}:51820
PersistentKeepalive = 25
"""
        with open(controller_config_path, "w") as f:
            f.write(controller_config_content)
        os.chmod(controller_config_path, stat.S_IRUSR | stat.S_IWUSR)
        logger.info(f"✅ Controller WireGuard config written: {controller_config_path}")
    wg_config_content = f"""[Interface]
{"Address = " + wg_interface_ip if wg_interface_ip else ""}
ListenPort = 51820
PrivateKey = {private_key}
""" + "".join(wg_peers)
    with open(wg_config_path, "w") as f:
        f.write(wg_config_content.strip() + "\n")
    os.chmod(wg_config_path, stat.S_IRUSR | stat.S_IWUSR)
    logger.info(f"✅ WireGuard server config written: {wg_config_path}")
    return wg_config_path, controller_config_path if EXPOSE_CONTAINERS else None

def get_container_ip(container_name):
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        networks = container.attrs["NetworkSettings"]["Networks"]
        for network_name, network_info in networks.items():
            if "IPAddress" in network_info and network_info["IPAddress"]:
                return network_info["IPAddress"]
        raise ValueError(f"❌ No valid IP address found for container '{container_name}'")
    except Exception as e:
        logger.error(f"❌ Error getting container IP for '{container_name}': {e}")
    return None

def add_peers_to_wireguard():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, ip_range, wg_public_key FROM sensos.networks;")
            networks = cur.fetchall()
            for network_id, network_name, ip_range, server_public_key in networks:
                wg_config_path = WG_CONFIG_DIR / f"{network_name}.conf"
                server_config = extract_server_config(wg_config_path)
                cur.execute(
                    """
                    SELECT p.wg_ip, k.wg_public_key 
                    FROM sensos.wireguard_peers p
                    JOIN sensos.wireguard_keys k ON p.id = k.peer_id
                    WHERE p.network_id = %s AND k.is_active = TRUE;
                    """, (network_id,)
                )
                peers = cur.fetchall()
                with open(wg_config_path, "w") as f:
                    f.write(server_config.strip() + "\n\n")
                    for wg_ip, wg_public_key in peers:
                        f.write(f"\n[Peer]\nPublicKey = {wg_public_key}\nAllowedIPs = {wg_ip}/32\n")
    logger.info("✅ WireGuard configuration regenerated for all networks.")

def extract_server_config(wg_config_path):
    if not os.path.exists(wg_config_path):
        raise FileNotFoundError(f"Config file {wg_config_path} does not exist. Cannot regenerate configuration.")
    with open(wg_config_path, "r") as f:
        config = f.read()
    match = re.search(r"^\s*\[\s*Interface\s*\](?:\n(?!^\s*\[\s*\w+\s*\]$)\s*.*)*", config, re.MULTILINE)
    if not match:
        raise ValueError(f"[Interface] not found in {wg_config_path}. Check the file format.")
    return match.group(0)

def get_last_assigned_ip(network_id: int) -> Optional[ipaddress.IPv4Address]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wg_ip FROM sensos.wireguard_peers WHERE network_id = %s ORDER BY wg_ip DESC LIMIT 1;",
                (network_id,),
            )
            result = cur.fetchone()
            return ipaddress.ip_address(result[0]) if result else None

def compute_next_ip(ip_range: ipaddress.IPv4Network, last_ip: Optional[ipaddress.IPv4Address] = None, subnet_offset: int = 0) -> Optional[ipaddress.IPv4Address]:
    base_bytes = bytearray(ip_range.network_address.packed)
    base_bytes[2] = subnet_offset
    base_bytes[3] = 0
    subnet_base = ipaddress.IPv4Address(bytes(base_bytes))
    subnet = ipaddress.ip_network(f"{subnet_base}/{ip_range.prefixlen}", strict=False)
    if last_ip is None or last_ip not in subnet:
        next_ip = subnet.network_address + 1
    else:
        next_ip = last_ip + 1
    return next_ip if next_ip < subnet.broadcast_address else None

def create_version_history_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.version_history (
            id SERIAL PRIMARY KEY,
            version_major TEXT NOT NULL,
            version_minor TEXT NOT NULL,
            version_patch TEXT NOT NULL,
            version_suffix TEXT,
            git_commit TEXT,
            git_branch TEXT,
            git_tag TEXT,
            git_dirty TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        );
        """
    )

def create_networks_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.networks (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            ip_range CIDR UNIQUE NOT NULL,
            wg_public_ip INET NOT NULL,
            wg_port INTEGER NOT NULL CHECK (wg_port > 0 AND wg_port <= 65535),
            wg_public_key TEXT UNIQUE NOT NULL,
            UNIQUE (wg_public_ip, wg_port)
        );
        """
    )

def create_wireguard_peers_table(cur):
    cur.execute(
        """
        CREATE EXTENSION IF NOT EXISTS "pgcrypto";
        CREATE TABLE IF NOT EXISTS sensos.wireguard_peers (
            id SERIAL PRIMARY KEY,
            uuid UUID NOT NULL DEFAULT gen_random_uuid(),
            network_id INTEGER REFERENCES sensos.networks(id) ON DELETE CASCADE,
            wg_ip INET UNIQUE NOT NULL,
            note TEXT DEFAULT NULL,
            registered_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(uuid)
        );
        """
    )

def create_wireguard_keys_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.wireguard_keys (
            id SERIAL PRIMARY KEY,
            peer_id INTEGER REFERENCES sensos.wireguard_peers(id) ON DELETE CASCADE,
            wg_public_key TEXT UNIQUE NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

def create_ssh_keys_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.ssh_keys (
            id SERIAL PRIMARY KEY,
            network_id INTEGER REFERENCES sensos.networks(id) ON DELETE CASCADE,
            peer_id INTEGER REFERENCES sensos.wireguard_peers(id) ON DELETE CASCADE,
            username TEXT NOT NULL,
            uid INTEGER NOT NULL,
            ssh_public_key TEXT NOT NULL,
            key_type TEXT NOT NULL,
            key_size INTEGER NOT NULL,
            key_comment TEXT,
            fingerprint TEXT NOT NULL,
            expires_at TIMESTAMP,
            last_used TIMESTAMP DEFAULT NOW(),
            UNIQUE (peer_id, ssh_public_key)
        );
        """
    )

def create_client_status_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.client_status (
            id SERIAL PRIMARY KEY,
            peer_id INTEGER REFERENCES sensos.wireguard_peers(id),
            last_check_in TIMESTAMP DEFAULT NOW(),
            uptime INTERVAL,
            cpu_usage FLOAT,
            memory_usage FLOAT,
            disk_usage FLOAT,
            version TEXT,
            error_count INTEGER,
            latency FLOAT,
            ip_address INET,
            temperature FLOAT,
            battery_level FLOAT,
            status_message TEXT
        );
        """
    )

def update_version_history_table(cur):
    cur.execute(
        """
        INSERT INTO sensos.version_history 
        (version_major, version_minor, version_patch, version_suffix, git_commit, git_branch, git_tag, git_dirty)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """,
        (
            VERSION_MAJOR,
            VERSION_MINOR,
            VERSION_PATCH,
            VERSION_SUFFIX,
            GIT_COMMIT,
            GIT_BRANCH,
            GIT_TAG,
            GIT_DIRTY,
        ),
    )

def create_hardware_profile_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.hardware_profiles (
            id SERIAL PRIMARY KEY,
            peer_id INTEGER REFERENCES sensos.wireguard_peers(id) ON DELETE CASCADE,
            profile_json JSONB NOT NULL,
            uploaded_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(peer_id)
        );
        """
    )

def create_peer_location_table(cur):
    cur.execute(
        """
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE TABLE IF NOT EXISTS sensos.peer_locations (
            id SERIAL PRIMARY KEY,
            peer_id INTEGER REFERENCES sensos.wireguard_peers(id) ON DELETE CASCADE,
            location GEOGRAPHY(POINT, 4326) NOT NULL,
            recorded_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

def create_initial_network(cur):
    network_name = os.getenv("INITIAL_NETWORK")
    if not network_name:
        logger.error("❌ INITIAL_NETWORK is not set in .env. Exiting.")
        return None
    cur.execute("SELECT id, ip_range, wg_public_ip, wg_port, wg_public_key FROM sensos.networks WHERE name = %s;", (network_name,))
    existing_network = cur.fetchone()
    if existing_network:
        network_id, ip_range, wg_public_ip, wg_port, wg_public_key = existing_network
        logger.info(f"✅ Network '{network_name}' already exists (ID: {network_id}).")
        if not wg_public_ip or not wg_port:
            wg_public_ip = resolve_hostname(os.getenv("WG_SERVER_IP", "127.0.0.1"))
            wg_port = int(os.getenv("WG_PORT", "51820"))
            cur.execute("UPDATE sensos.networks SET wg_public_ip = %s, wg_port = %s WHERE id = %s;", (wg_public_ip, wg_port, network_id))
            logger.info(f"✅ Network '{network_name}' updated with IP {wg_public_ip}, port {wg_port}.")
    else:
        logger.info(f"Creating network '{network_name}'...")
        try:
            result = create_network_entry(cur, network_name)
        except RuntimeError as e:
            logger.critical(f"❌ Failed to initialize network '{network_name}': {e}")
            raise
        add_peers_to_wireguard()
        restart_container("sensos-wireguard")
        restart_container("sensos-api-proxy")
        network_id = result["id"]
    return network_id
