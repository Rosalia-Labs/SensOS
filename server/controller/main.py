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
from fastapi import FastAPI, Depends, HTTPException, Form, BackgroundTasks
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, status
from psycopg.errors import UniqueViolation
from typing import Tuple, Optional, Union
from pydantic import BaseModel, IPvAnyAddress
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WG_CONFIG_DIR = Path("/config/wg_confs")
CONTROLLER_CONFIG_DIR = Path("/etc/wireguard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Called lifespan async context manager...")

    # Startup code here
    logger.info("Starting up!")

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                logger.info("Creating schema 'sensos' if not exists...")
                cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
                cur.execute("set search_path to sensos, public;")
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

    yield  # Application runs here

    # Optional shutdown logic can go here
    logger.info("Shutting down!")


app = FastAPI(lifespan=lifespan)

security = HTTPBasic()

POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@sensos-database/postgres"
)

if not POSTGRES_PASSWORD:
    raise ValueError("POSTGRES_PASSWORD is not set. Exiting.")

API_PASSWORD = os.getenv("API_PASSWORD")

if not API_PASSWORD:
    raise ValueError("API_PASSWORD is not set. Exiting.")

# Retrieve versioning info
VERSION_MAJOR = os.getenv("VERSION_MAJOR", "Unknown")
VERSION_MINOR = os.getenv("VERSION_MINOR", "Unknown")
VERSION_PATCH = os.getenv("VERSION_PATCH", "Unknown")
VERSION_SUFFIX = os.getenv("VERSION_SUFFIX", "")
GIT_COMMIT = os.getenv("GIT_COMMIT", "Unknown")
GIT_BRANCH = os.getenv("GIT_BRANCH", "Unknown")
GIT_TAG = os.getenv("GIT_TAG", "Unknown")
GIT_DIRTY = os.getenv("GIT_DIRTY", "false")

EXPOSE_CONTAINERS = os.getenv("EXPOSE_CONTAINERS", "false").lower() == "true"


def resolve_hostname(value):
    """Return an IP address for a given hostname or IP string."""
    try:
        # Check if the value is already a valid IP address (IPv4 or IPv6)
        socket.inet_pton(socket.AF_INET, value)  # Check IPv4
        return value  # It's already an IPv4 address
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, value)  # Check IPv6
            return value  # It's already an IPv6 address
        except OSError:
            pass  # Not an IP, try resolving as a hostname

    try:
        # Resolve hostname to an IP (IPv4 or IPv6)
        addr_info = socket.getaddrinfo(value, None, family=socket.AF_UNSPEC)
        for family, _, _, _, sockaddr in addr_info:
            if family in (socket.AF_INET, socket.AF_INET6):
                return sockaddr[0]  # Return the first valid IP
    except socket.gaierror:
        pass  # Failed to resolve

    return None  # Return None if resolution fails


# Log versioning details at startup
logger.info("🔍 Application Version Information:")
logger.info(
    f"   Version: {VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}{('-' + VERSION_SUFFIX) if VERSION_SUFFIX else ''}"
)
logger.info(f"   Git Commit: {GIT_COMMIT}")
logger.info(f"   Git Branch: {GIT_BRANCH}")
logger.info(f"   Git Tag: {GIT_TAG}")
logger.info(f"   Git Dirty: {'✅ Clean' if GIT_DIRTY == 'false' else '⚠️ Dirty'}")


def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials


def get_network_details(network_name):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ip_range, wg_public_key, wg_public_ip, wg_port
                FROM sensos.networks
                WHERE name = %s
                """,
                (network_name,),
            )
            return cur.fetchone()


def get_last_assigned_ip(network_id: int) -> Optional[ipaddress.IPv4Address]:
    """Fetch the highest assigned IP address for the given network."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wg_ip FROM sensos.wireguard_peers WHERE network_id = %s ORDER BY wg_ip DESC LIMIT 1;",
                (network_id,),
            )
            result = cur.fetchone()
            return ipaddress.ip_address(result[0]) if result else None


def compute_next_ip(
    ip_range: ipaddress.IPv4Network,
    last_ip: Optional[ipaddress.IPv4Address] = None,
    subnet_offset: int = 0,
) -> Optional[ipaddress.IPv4Address]:
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


def extract_server_config(wg_config_path):
    """Extracts the server's WireGuard config from an existing file, preserving PrivateKey."""
    if not os.path.exists(wg_config_path):
        raise FileNotFoundError(
            f"Config file {wg_config_path} does not exist. Cannot regenerate configuration."
        )

    with open(wg_config_path, "r") as f:
        config = f.read()

    # Match `[Interface]` with optional leading spaces, capturing everything until the next section `[SomeSection]`
    match = re.search(
        r"^\s*\[\s*Interface\s*\](?:\n(?!^\s*\[\s*\w+\s*\]$)\s*.*)*",
        config,
        re.MULTILINE,
    )

    if not match:
        raise ValueError(
            f"[Interface] not found in {wg_config_path}. Check the file format."
        )

    return match.group(0)  # Return the `[Interface]` section


def get_docker_client():
    """Returns a Docker client that works on both Linux and Windows."""
    if platform.system() == "Windows":
        docker_host = "npipe:////./pipe/docker_engine"  # Windows Named Pipe
    else:
        docker_host = "unix://var/run/docker.sock"  # Default Unix Socket

    return docker.DockerClient(base_url=docker_host)


def get_container_ip(container_name):
    """Retrieve the IP address of a running container using Docker SDK."""
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        networks = container.attrs["NetworkSettings"]["Networks"]

        # Extract the IP from the correct network
        for network_name, network_info in networks.items():
            if "IPAddress" in network_info and network_info["IPAddress"]:
                return network_info["IPAddress"]

        raise ValueError(
            f"❌ No valid IP address found for container '{container_name}'"
        )

    except docker.errors.NotFound:
        print(f"❌ Container '{container_name}' not found.")
    except docker.errors.APIError as e:
        print(f"❌ Docker API error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error while getting IP for '{container_name}': {e}")

    return None


def restart_container(container_name: str):
    """Restarts the WireGuard container, supporting both Linux and Windows."""
    client = get_docker_client()

    try:
        container = client.containers.get(container_name)
        if container.status != "running":
            logger.warning(
                f"WireGuard container '{container_name}' is not running but will be restarted."
            )
        container.restart()
        logger.info(f"WireGuard container '{container_name}' restarted successfully.")
    except docker.errors.NotFound:
        logger.error(f"WireGuard container '{container_name}' not found.")
    except docker.errors.APIError as e:
        logger.error(f"Error restarting WireGuard container '{container_name}': {e}")
    except Exception as e:
        logger.exception(f"Unexpected error while restarting WireGuard container: {e}")


def start_controller_wireguard():
    """Ensures all WireGuard configs in /etc/wireguard are loaded or updated."""
    logger.info("🔍 Scanning /etc/wireguard for existing WireGuard configurations...")

    config_files = sorted(CONTROLLER_CONFIG_DIR.glob("*.conf"))
    if not config_files:
        logger.warning("⚠️ No WireGuard config files found in /etc/wireguard.")
        return

    for config_file in config_files:
        network_name = config_file.stem  # e.g., 'wg0' from 'wg0.conf'

        result = subprocess.run(
            ["wg", "show", network_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            logger.info(f"🔄 Updating running WireGuard interface: {network_name}")
            try:
                strip_result = subprocess.run(
                    ["wg-quick", "strip", network_name],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                syncconf = subprocess.run(
                    ["wg", "syncconf", network_name, "/dev/stdin"],
                    input=strip_result.stdout,
                    text=True,
                    check=True,
                )
                logger.info(f"✅ Updated WireGuard interface: {network_name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"❌ Failed to update {network_name}: {e}")
        else:
            logger.info(f"🚀 Enabling new WireGuard interface: {network_name}")
            try:
                subprocess.run(["wg-quick", "up", network_name], check=True)
                logger.info(f"✅ Activated WireGuard interface: {network_name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"❌ Failed to activate {network_name}: {e}")

    logger.info("✅ WireGuard interfaces setup complete.")


def get_db(retries=10, delay=3):
    """Retries the database connection to avoid startup race conditions."""
    for attempt in range(retries):
        try:
            return psycopg.connect(DATABASE_URL, autocommit=True)
        except psycopg.OperationalError:
            if attempt == retries - 1:
                raise
            logger.info(
                f"Database not ready, retrying in {delay} seconds... (Attempt {attempt + 1}/{retries})"
            )
            time.sleep(delay)


def generate_default_ip_range(name):
    """Simple deterministic function to generate a /16 subnet."""
    hash_val = sum(ord(c) for c in name) % 256
    return f"10.{hash_val}.0.0/16"


def generate_wireguard_keys():
    """Generates and returns a WireGuard private/public key pair."""
    private_key = subprocess.run(
        "wg genkey", shell=True, capture_output=True, text=True
    ).stdout.strip()
    public_key = subprocess.run(
        f"echo {private_key} | wg pubkey", shell=True, capture_output=True, text=True
    ).stdout.strip()
    return private_key, public_key


def insert_peer(
    network_id: int, wg_ip: str, note: Optional[str] = None
) -> Tuple[int, str]:
    """Insert a new peer and return (id, uuid)."""
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
    """Registers a WireGuard key for an existing peer in the database."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM sensos.wireguard_peers WHERE wg_ip = %s;",
                (wg_ip,),
            )
            peer = cur.fetchone()
            if not peer:
                return None

            peer_id = peer[0]  # Extract the integer ID from the tuple
            cur.execute(
                "INSERT INTO sensos.wireguard_keys (peer_id, wg_public_key) VALUES (%s, %s);",
                (peer_id, wg_public_key),
            )

    return {"wg_ip": wg_ip, "wg_public_key": wg_public_key}


def create_network_entry(cur, name, wg_public_ip=None, wg_port=None):
    wg_public_ip = wg_public_ip or resolve_hostname(
        os.getenv("WG_SERVER_IP", "127.0.0.1")
    )
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

        # Only perform full WireGuard setup for a new network.
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
        logger.warning(
            f"⚠️ Unique constraint violated when creating network '{name}': {e}"
        )

        # Identify which constraint failed
        if "networks_name_key" in str(e):
            cur.execute(
                "SELECT id, ip_range, wg_public_ip, wg_port, wg_public_key FROM sensos.networks WHERE name = %s;",
                (name,),
            )
        elif "networks_ip_range_key" in str(e):
            cur.execute(
                "SELECT id, name, wg_public_ip, wg_port, wg_public_key FROM sensos.networks WHERE ip_range = %s;",
                (ip_range,),
            )
        elif "networks_wg_public_key_key" in str(e):
            cur.execute(
                "SELECT id, name, ip_range, wg_public_ip, wg_port FROM sensos.networks WHERE wg_public_key = %s;",
                (public_key,),
            )
        elif "networks_wg_public_ip_wg_port_key" in str(e):
            cur.execute(
                "SELECT id, name, ip_range, wg_public_key FROM sensos.networks WHERE wg_public_ip = %s AND wg_port = %s;",
                (wg_public_ip, wg_port),
            )
        else:
            raise RuntimeError(
                "Constraint violation but failed to retrieve existing network."
            )

        existing_network = cur.fetchone()
        if not existing_network:
            raise RuntimeError(
                "Constraint violation but failed to retrieve existing network."
            )

        # Map result
        result = {
            "id": existing_network[0],
            "name": existing_network[1] if len(existing_network) > 1 else name,
            "ip_range": existing_network[2] if len(existing_network) > 2 else ip_range,
            "wg_public_ip": (
                existing_network[3] if len(existing_network) > 3 else wg_public_ip
            ),
            "wg_port": existing_network[4] if len(existing_network) > 4 else wg_port,
            "wg_public_key": (
                existing_network[5] if len(existing_network) > 5 else public_key
            ),
        }

        # ✅ Refuse to overwrite the key
        if result["wg_public_key"] != public_key:
            raise RuntimeError(
                f"❌ Refusing to overwrite existing WireGuard key for network '{name}'. "
                f"The database is authoritative. Restore the original key or delete the network first."
            )

        logger.info(f"✅ Returning existing network: {result}")
        return result


def create_wireguard_configs(
    network_id: int, name: str, ip_range: str, private_key: str, wg_public_key: str
):
    WG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    wg_config_path = WG_CONFIG_DIR / f"{name}.conf"
    controller_config_path = CONTROLLER_CONFIG_DIR / f"{name}.conf"

    base_ip = ip_range.split("/")[0]
    network_prefix = ".".join(base_ip.split(".")[:3])

    # API Proxy always assigned prefix.1
    api_proxy_ip = f"{network_prefix}.1"
    insert_peer(network_id, api_proxy_ip, "API server")
    # (No API proxy public key yet; will be registered later.)

    wg_interface_ip = ""
    wg_peers = []

    if EXPOSE_CONTAINERS:
        # WireGuard server container IP is prefix.2
        wg_interface_ip = f"{network_prefix}.2/16"

        # Controller IP is prefix.3
        controller_ip = f"{network_prefix}.3/16"
        controller_private_key, controller_public_key = generate_wireguard_keys()

        # Register Controller as peer
        insert_peer(network_id, controller_ip.split("/")[0])
        register_wireguard_key_in_db(controller_ip.split("/")[0], controller_public_key)

        # WireGuard container (server) config will include controller peer
        wg_peers.append(
            f"""
[Peer]
PublicKey = {controller_public_key}
AllowedIPs = {controller_ip.split('/')[0]}/32
"""
        )

        wireguard_container_ip = get_container_ip("sensos-wireguard")

        # Controller's own WireGuard config
        controller_config_content = f"""[Interface]
Address = {controller_ip}
PrivateKey = {controller_private_key}

[Peer]
PublicKey = {wg_public_key}
AllowedIPs = {ip_range}
Endpoint = {wireguard_container_ip}:51820
PersistentKeepalive = 25
"""

        # Write controller config file
        with open(controller_config_path, "w") as f:
            f.write(controller_config_content)
        os.chmod(controller_config_path, stat.S_IRUSR | stat.S_IWUSR)
        logger.info(f"✅ Controller WireGuard config written: {controller_config_path}")

    # WireGuard server container config (written by controller)
    wg_config_content = f"""[Interface]
{"Address = " + wg_interface_ip if wg_interface_ip else ""}
ListenPort = 51820
PrivateKey = {private_key}
""" + "".join(
        wg_peers
    )

    # Write server config file
    with open(wg_config_path, "w") as f:
        f.write(wg_config_content.strip() + "\n")
    os.chmod(wg_config_path, stat.S_IRUSR | stat.S_IWUSR)
    logger.info(f"✅ WireGuard server config written: {wg_config_path}")

    return wg_config_path, controller_config_path if EXPOSE_CONTAINERS else None


def add_peers_to_wireguard():
    """Regenerates all WireGuard configuration files based on the database."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, ip_range, wg_public_key FROM sensos.networks;"
            )
            networks = cur.fetchall()

            for network_id, network_name, ip_range, server_public_key in networks:

                wg_config_path = WG_CONFIG_DIR / f"{network_name}.conf"

                # Extract existing server config to preserve the PrivateKey
                server_config = extract_server_config(wg_config_path)

                # Collect all active peers and their WireGuard keys for this network
                cur.execute(
                    """
                    SELECT p.wg_ip, k.wg_public_key 
                    FROM sensos.wireguard_peers p
                    JOIN sensos.wireguard_keys k ON p.id = k.peer_id
                    WHERE p.network_id = %s AND k.is_active = TRUE;
                    """,
                    (network_id,),
                )
                peers = cur.fetchall()

                os.chmod(wg_config_path, stat.S_IRUSR | stat.S_IWUSR)
                with open(wg_config_path, "w") as f:
                    f.write(server_config.strip() + "\n\n")

                    for wg_ip, wg_public_key in peers:
                        f.write(
                            f"""
[Peer]
PublicKey = {wg_public_key}
AllowedIPs = {wg_ip}/32
"""
                        )

    logger.info(
        "✅ WireGuard configuration regenerated for all networks with secure permissions."
    )


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
            os.getenv("VERSION_MAJOR", "Unknown"),
            os.getenv("VERSION_MINOR", "Unknown"),
            os.getenv("VERSION_PATCH", "Unknown"),
            os.getenv("VERSION_SUFFIX", ""),
            os.getenv("GIT_COMMIT", "Unknown"),
            os.getenv("GIT_BRANCH", "Unknown"),
            os.getenv("GIT_TAG", "Unknown"),
            os.getenv("GIT_DIRTY", "false"),
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

    cur.execute(
        "SELECT id, ip_range, wg_public_ip, wg_port, wg_public_key FROM sensos.networks WHERE name = %s;",
        (network_name,),
    )
    existing_network = cur.fetchone()

    if existing_network:
        network_id, ip_range, wg_public_ip, wg_port, wg_public_key = existing_network
        logger.info(f"✅ Network '{network_name}' already exists (ID: {network_id}).")

        # Optionally update missing details
        if not wg_public_ip or not wg_port:
            wg_public_ip = resolve_hostname(os.getenv("WG_SERVER_IP", "127.0.0.1"))
            wg_port = int(os.getenv("WG_PORT", "51820"))
            cur.execute(
                "UPDATE sensos.networks SET wg_public_ip = %s, wg_port = %s WHERE id = %s;",
                (wg_public_ip, wg_port, network_id),
            )
            logger.info(
                f"✅ Network '{network_name}' updated with IP {wg_public_ip}, port {wg_port}."
            )
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


@app.get("/", response_class=HTMLResponse)
def dashboard(credentials: HTTPBasicCredentials = Depends(authenticate)):
    """Display network status with version information in the footer."""

    # Fetch latest version entry
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM sensos.version_history ORDER BY timestamp DESC LIMIT 1;"
            )
            version_info = cur.fetchone()

            # Fetch list of networks
            cur.execute(
                "SELECT name, ip_range, wg_public_ip, wg_port FROM sensos.networks ORDER BY name;"
            )
            networks = cur.fetchall()

    # Handle missing networks
    if networks:
        network_table = """
        <h3>🌐 Registered Networks</h3>
        <table>
            <tr>
                <th>Network Name</th>
                <th>IP Range</th>
                <th>Public IP</th>
                <th>Port</th>
            </tr>
        """
        for network in networks:
            network_table += f"""
            <tr>
                <td>{network[0]}</td>
                <td>{network[1]}</td>
                <td>{network[2]}</td>
                <td>{network[3]}</td>
            </tr>
            """
        network_table += "</table>"
    else:
        network_table = "<p style='color: red;'>⚠️ No registered networks found.</p>"

    # Handle missing version info (Footer)
    if version_info:
        version_display = f"""
        <footer>
            <p><strong>🔍 Version Information</strong></p>
            <table>
                <tr><th>Version</th><td>{version_info[1]}.{version_info[2]}.{version_info[3]}{('-' + version_info[4]) if version_info[4] else ''}</td></tr>
                <tr><th>Git Commit</th><td>{version_info[5]}</td></tr>
                <tr><th>Git Branch</th><td>{version_info[6]}</td></tr>
                <tr><th>Git Tag</th><td>{version_info[7]}</td></tr>
                <tr><th>Git Dirty</th><td>{"✅ Clean" if version_info[8] == "false" else "⚠️ Dirty"}</td></tr>
                <tr><th>Timestamp</th><td>{version_info[9]}</td></tr>
            </table>
        </footer>
        """
    else:
        version_display = "<footer><p style='color: red;'>⚠️ No version information available.</p></footer>"

    return f"""
    <html>
    <head>
        <title>Sensor Network Manager</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background-color: #f7f7f7;
                color: #333;
                margin: 0;
                padding: 20px;
                display: flex;
                justify-content: center;
            }}
            .container {{
                max-width: 800px; /* Adjust width here */
                width: 90%; /* Ensure it scales well on smaller screens */
                background: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            }}
            h2, h3 {{
                color: #005a9c;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 10px 0;
                background: white;
            }}
            th, td {{
                padding: 10px;
                border: 1px solid #ddd;
                text-align: left;
            }}
            th {{
                background-color: #005a9c;
                color: white;
            }}
            footer {{
                margin-top: 20px;
                padding-top: 10px;
                border-top: 2px solid #ddd;
                font-size: 14px;
                color: #666;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Sensor Network Manager</h2>
            <h3>📡 Network Overview</h3>
            <p>Welcome to the Sensor Network Dashboard.</p>
            {network_table}
            {version_display}
        </div>
    </body>
    </html>
    """


@app.post("/create-network")
def create_network(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(authenticate),
    name: str = Form(...),
    wg_public_ip: Optional[str] = Form(None),
    wg_port: Optional[str] = Form(None),
):
    try:
        wg_port = int(wg_port) if wg_port else int(os.getenv("WG_PORT", "51820"))
        if not (1 <= wg_port <= 65535):
            raise ValueError()
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid WireGuard port. Must be between 1 and 65535."},
        )

    try:
        with get_db() as conn:
            result = create_network_entry(conn.cursor(), name, wg_public_ip, wg_port)
            logger.info(f"create_network_entry returned: {result}")
            start_controller_wireguard()

        # Schedule the restart of the API proxy container in the background
        background_tasks.add_task(restart_container, "sensos-wireguard")
        background_tasks.add_task(restart_container, "sensos-api-proxy")

        return result

    except RuntimeError as e:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": str(e)},
        )


@app.get("/list-peers", response_class=HTMLResponse)
def list_peers(credentials: HTTPBasicCredentials = Depends(authenticate)):
    """Displays a web page listing all registered WireGuard peers."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.wg_ip, n.name AS network_name
                FROM sensos.wireguard_peers p
                JOIN sensos.networks n ON p.network_id = n.id
                ORDER BY n.name, p.wg_ip;
                """
            )
            peers = cur.fetchall()

    # Generate an HTML table
    peer_table = """
    <html>
    <head>
        <title>Registered WireGuard Peers</title>
        <style>
            body { font-family: Arial, sans-serif; }
            table { width: 80%%; border-collapse: collapse; margin: 20px auto; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h2 style="text-align: center;">Registered WireGuard Peers</h2>
        <table>
            <tr>
                <th>WireGuard IP</th>
                <th>Network Name</th>
            </tr>
    """

    for row in peers:
        wg_ip, network_name = row
        peer_table += f"""
        <tr>
            <td>{wg_ip}</td>
            <td>{network_name}</td>
        </tr>
        """

    peer_table += """
        </table>
    </body>
    </html>
    """

    return HTMLResponse(content=peer_table)


class RegisterPeerRequest(BaseModel):
    network_name: str
    subnet_offset: int = 0
    note: Optional[str] = None


@app.post("/register-peer")
def register_peer(
    request: RegisterPeerRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Registers a new peer, computes IP within a subnetwork, and returns the network's public key and connection details."""
    network_details = get_network_details(request.network_name)
    if not network_details:
        return JSONResponse(
            status_code=404,
            content={"error": f"Network '{request.network_name}' not found."},
        )

    network_id, subnet, public_key, wg_public_ip, wg_port = network_details

    # Ensure subnet_offset is within range
    network = ipaddress.ip_network(subnet, strict=False)
    if (
        request.subnet_offset < 0
        or request.subnet_offset >= network.num_addresses // 256
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Invalid subnet_offset {request.subnet_offset}. Must be between 0 and {network.num_addresses // 256 - 1}."
            },
        )

    # Compute the first IP in the specified subnetwork (x.x.<subnet_offset>.1)
    last_ip = get_last_assigned_ip(network_id)
    wg_ip = compute_next_ip(subnet, last_ip, request.subnet_offset)

    if not wg_ip:
        return JSONResponse(
            status_code=409,
            content={"error": f"No available IPs in subnet {request.subnet_offset}."},
        )

    peer_id, peer_uuid = insert_peer(network_id, wg_ip, note=request.note)

    return {
        "wg_ip": wg_ip,
        "wg_public_key": public_key,
        "wg_public_ip": wg_public_ip,
        "wg_port": wg_port,
        "peer_uuid": peer_uuid,
    }


class RegisterWireguardKeyRequest(BaseModel):
    wg_ip: str
    wg_public_key: str


@app.post("/register-wireguard-key")
def register_wireguard_key(
    request: RegisterWireguardKeyRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Endpoint that registers a WireGuard key for an existing peer."""
    result = register_wireguard_key_in_db(request.wg_ip, request.wg_public_key)

    if result is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Peer '{request.wg_ip}' not found."},
        )

    add_peers_to_wireguard()
    restart_container("sensos-wireguard")

    return result


class RegisterSSHKeyRequest(BaseModel):
    wg_ip: str  # Replace network_id and peer_id with wg_ip
    username: str
    uid: int
    ssh_public_key: str
    key_type: str  # e.g., 'ed25519', 'rsa', 'ecdsa'
    key_size: int  # e.g., 2048, 4096 (for RSA)
    key_comment: Optional[str] = None  # Optional, e.g., 'user@hostname'
    fingerprint: str  # Unique fingerprint of the key
    expires_at: Optional[datetime] = None  # Optional expiration date


@app.post("/exchange-ssh-keys")
def exchange_ssh_keys(
    request: RegisterSSHKeyRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Registers an SSH public key for a peer."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Lookup network_id and peer_id using wg_ip
            cur.execute(
                """
                SELECT network_id, id FROM sensos.wireguard_peers WHERE wg_ip = %s;
                """,
                (request.wg_ip,),
            )
            result = cur.fetchone()

            if not result:
                raise HTTPException(
                    status_code=404,
                    detail=f"Peer with WireGuard IP '{request.wg_ip}' not found.",
                )

            network_id, peer_id = result  # Extract network and peer IDs

            # Insert the SSH public key with all relevant fields
            cur.execute(
                """
                INSERT INTO sensos.ssh_keys 
                (network_id, peer_id, username, uid, ssh_public_key, key_type, key_size, 
                 key_comment, fingerprint, expires_at, last_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (peer_id, ssh_public_key) DO NOTHING
                RETURNING *;
                """,
                (
                    network_id,
                    peer_id,
                    request.username,
                    request.uid,
                    request.ssh_public_key,
                    request.key_type,
                    request.key_size,
                    request.key_comment,
                    request.fingerprint,
                    request.expires_at,
                ),
            )

            inserted_key = cur.fetchone()  # Check if insertion was successful

            if not inserted_key:
                raise HTTPException(
                    status_code=409, detail="SSH key already exists for this peer."
                )

        conn.commit()  # Ensure the change is committed

    ssh_public_key_path = "/home/sensos/.ssh/id_ed25519.pub"

    if not os.path.exists(ssh_public_key_path):
        raise HTTPException(status_code=404, detail="SSH public key not found.")

    try:
        with open(ssh_public_key_path, "r") as key_file:
            ssh_public_key = key_file.read().strip()

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading SSH public key: {str(e)}"
        )

    return {
        "ssh_public_key": ssh_public_key,
    }


@app.get("/inspect-database", response_class=HTMLResponse)
def inspect_database(
    limit: int = 10,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Inspect all database tables in a single formatted HTML output."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Get all table names
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'sensos'
                ORDER BY table_name;
                """
            )
            tables = [row[0] for row in cur.fetchall()]

            if not tables:
                return HTMLResponse("<h3>⚠️ No tables found in the database.</h3>")

            html = """
            <html>
            <head>
                <title>Database Inspection</title>
                <style>
                    body { font-family: Arial, sans-serif; margin: 20px; }
                    .container { width: 90%; margin: auto; }
                    .table-container { margin-bottom: 30px; }
                    h2 { text-align: center; }
                    summary { font-size: 18px; font-weight: bold; cursor: pointer; padding: 5px; }
                    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                    th { background-color: #f2f2f2; }
                    details { margin-bottom: 20px; }
                </style>
            </head>
            <body>
                <h2>📊 Database Inspection</h2>
                <div class="container">
            """

            for table in tables:
                cur.execute(f"SELECT * FROM sensos.{table} LIMIT %s;", (limit,))
                rows = cur.fetchall()
                column_names = [desc[0] for desc in cur.description]

                html += f"""
                <details class="table-container" open>
                    <summary>📂 Table: <code>{table}</code> (Showing max {limit} rows)</summary>
                    <table>
                        <tr>
                """
                html += "".join(f"<th>{col}</th>" for col in column_names)
                html += "</tr>"

                if rows:
                    for row in rows:
                        html += (
                            "<tr>"
                            + "".join(f"<td>{cell}</td>" for cell in row)
                            + "</tr>"
                        )
                else:
                    html += "<tr><td colspan='100%' style='text-align:center;'>⚠️ No data in this table</td></tr>"

                html += "</table></details>"

            html += "</div></body></html>"
            return HTMLResponse(html)


@app.get("/get-peer-info")
def get_peer_info(
    ip_address: str, credentials: HTTPBasicCredentials = Depends(authenticate)
):
    """
    Given an IP address, returns:
      - exists: True if the IP is registered as a peer; otherwise False.
      - network_name: the name of the network the peer is registered to, or None.
      - network_wg_public_key: the WireGuard public key of the network, or None.
      - peer_wg_public_key: the WireGuard public key stored for the peer, or None.
      - ssh_public_key: the SSH public key associated with the peer, or None.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # Check if the IP exists in the wireguard_peers table
            cur.execute(
                "SELECT id, network_id FROM sensos.wireguard_peers WHERE wg_ip = %s;",
                (ip_address,),
            )
            peer = cur.fetchone()
            if not peer:
                return {
                    "exists": False,
                    "network_name": None,
                    "network_wg_public_key": None,
                    "peer_wg_public_key": None,
                    "ssh_public_key": None,
                }
            peer_id, network_id = peer

            # Get network details from the networks table
            cur.execute(
                "SELECT name, wg_public_key FROM sensos.networks WHERE id = %s;",
                (network_id,),
            )
            network = cur.fetchone()
            if network:
                network_name, network_wg_public_key = network
            else:
                network_name, network_wg_public_key = None, None

            # Get the peer's WireGuard public key from wireguard_keys table
            cur.execute(
                "SELECT wg_public_key FROM sensos.wireguard_keys WHERE peer_id = %s AND is_active = TRUE ORDER BY created_at DESC LIMIT 1;",
                (peer_id,),
            )
            peer_wg_row = cur.fetchone()
            peer_wg_public_key = peer_wg_row[0] if peer_wg_row else None

            # Get the associated SSH public key from the ssh_keys table
            cur.execute(
                "SELECT ssh_public_key FROM sensos.ssh_keys WHERE peer_id = %s ORDER BY last_used DESC LIMIT 1;",
                (peer_id,),
            )
            ssh_row = cur.fetchone()
            ssh_public_key = ssh_row[0] if ssh_row else None

    return {
        "exists": True,
        "network_name": network_name,
        "network_wg_public_key": network_wg_public_key,
        "peer_wg_public_key": peer_wg_public_key,
        "ssh_public_key": ssh_public_key,
    }


class ClientStatusRequest(BaseModel):
    client_id: int
    uptime: Optional[timedelta] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    version: Optional[str] = None
    error_count: Optional[int] = None
    latency: Optional[float] = None
    ip_address: Optional[IPvAnyAddress] = None
    temperature: Optional[float] = None
    battery_level: Optional[float] = None
    status_message: Optional[str] = None


@app.post("/client-status")
def client_status(
    status: ClientStatusRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """
    Endpoint for clients to send periodic check-in information.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sensos.client_status (
                    client_id, last_check_in, uptime, cpu_usage, memory_usage, disk_usage,
                    version, error_count, latency, ip_address, temperature, battery_level, status_message
                ) VALUES (
                    %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                );
                """,
                (
                    status.client_id,
                    status.uptime,
                    status.cpu_usage,
                    status.memory_usage,
                    status.disk_usage,
                    status.version,
                    status.error_count,
                    status.latency,
                    str(status.ip_address) if status.ip_address else None,
                    status.temperature,
                    status.battery_level,
                    status.status_message,
                ),
            )
            conn.commit()
    return {"message": "Client status updated successfully"}


@app.get("/get-wireguard-network-names")
def get_defined_networks():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM sensos.networks;")
            network_names = [row[0] for row in cur.fetchall()]
    return {"networks": network_names}


@app.get("/get-network-info")
def get_network_info(
    network_name: str, credentials: HTTPBasicCredentials = Depends(authenticate)
):
    """Retrieve all details for a given network, excluding the database ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, ip_range, wg_public_ip, wg_port, wg_public_key
                FROM sensos.networks
                WHERE name = %s;
                """,
                (network_name,),
            )
            result = cur.fetchone()

    if not result:
        return JSONResponse(
            status_code=404,
            content={"error": f"No network found with name '{network_name}'"},
        )

    return {
        "name": result[0],
        "ip_range": result[1],
        "wg_public_ip": result[2],
        "wg_port": result[3],
        "wg_public_key": result[4],
    }


class HardwareProfile(BaseModel):
    wg_ip: str  # Used internally to link to peer
    hostname: str
    model: str
    kernel_version: str
    cpu: dict
    firmware: dict
    memory: dict
    disks: dict
    usb_devices: str
    network_interfaces: dict


@app.post("/upload-hardware-profile")
def upload_hardware_profile(
    profile: HardwareProfile,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    profile_data = profile.dict()
    wg_ip = profile_data.pop("wg_ip")  # Extract wg_ip from profile

    with get_db() as conn:
        with conn.cursor() as cur:
            # Internally fetch peer_id; do not expose it
            cur.execute(
                "SELECT id FROM sensos.wireguard_peers WHERE wg_ip = %s;", (wg_ip,)
            )
            peer = cur.fetchone()

            if not peer:
                raise HTTPException(
                    status_code=404, detail=f"Peer with IP '{wg_ip}' not found."
                )

            peer_id = peer[0]

            # Store hardware profile linked internally via peer_id
            cur.execute(
                """
                INSERT INTO sensos.hardware_profiles (peer_id, profile_json)
                VALUES (%s, %s)
                ON CONFLICT (peer_id) DO UPDATE
                SET profile_json = EXCLUDED.profile_json, uploaded_at = NOW();
                """,
                (peer_id, json.dumps(profile_data)),
            )
            conn.commit()

    logger.info(f"✅ Hardware profile stored for peer IP '{wg_ip}'.")

    return {"status": "success", "wg_ip": wg_ip}


def signal_wireguard_container(signal_name="SIGUSR1"):
    try:
        container = get_docker_client().containers.get("sensos-wireguard")
        container.kill(signal=signal_name)
        logger.info(f"📣 Sent {signal_name} to sensos-wireguard container.")
    except docker.errors.NotFound:
        logger.error("❌ WireGuard container not found.")
    except Exception as e:
        logger.exception(f"❌ Failed to send signal to WireGuard container: {e}")


@app.get("/wireguard-status", response_class=HTMLResponse)
def wireguard_status_dashboard(
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """
    Displays an HTML dashboard showing WireGuard peer status for all active interfaces.
    Falls back to a warning if no status files are found.
    """
    signal_wireguard_container("SIGUSR1")
    status_files = sorted(Path("/config").glob("wireguard_status_*.txt"))
    if not status_files:
        return HTMLResponse(
            """
            <html>
            <head><title>WireGuard Status</title></head>
            <body>
                <h2 style='color: red;'>⚠️ No wireguard_status_*.txt files found.</h2>
                <p>The background service may not be running or has not yet written any status updates.</p>
            </body>
            </html>
            """,
            status_code=200,
        )

    def parse_peers(output: str):
        lines = output.strip().splitlines()
        peers = []
        current_peer = {}
        skip_interface = True

        for line in lines:
            line = line.strip()
            if skip_interface:
                if line.startswith("peer:"):
                    skip_interface = False
                else:
                    continue

            if line.startswith("peer:"):
                if current_peer:
                    peers.append(current_peer)
                current_peer = {"public_key": line.split(":", 1)[1].strip()}
            elif ":" in line:
                key, val = map(str.strip, line.split(":", 1))
                current_peer[key] = val

        if current_peer:
            peers.append(current_peer)

        return peers

    def parse_handshake(text):
        match = re.match(r"(\d+)\s+(\w+)\s+ago", text)
        if not match:
            return text
        num, unit = match.groups()
        try:
            delta = timedelta(**{unit: int(num)})
            ts = datetime.utcnow() - delta
            return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            return text

    html = """
    <html>
    <head>
        <title>WireGuard Status</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f7f7f7; padding: 20px; }
            h2 { color: #005a9c; }
            h3 { color: #333; margin-top: 40px; }
            table { width: 100%; border-collapse: collapse; background: white; margin-top: 10px; }
            th, td { border: 1px solid #ccc; padding: 10px; text-align: left; }
            th { background: #005a9c; color: white; }
            tr:nth-child(even) { background: #f2f2f2; }
        </style>
    </head>
    <body>
        <h2>🔐 WireGuard Peer Status</h2>
    """

    for status_path in status_files:
        interface_name = status_path.stem.replace("wireguard_status_", "")
        try:
            output = status_path.read_text()
        except Exception as e:
            html += f"<h3 style='color: red;'>❌ Failed to read {status_path.name}: {e}</h3>"
            continue

        peers = parse_peers(output)

        html += f"<h3>Interface: <code>{interface_name}</code></h3>"
        html += """
        <table>
            <tr>
                <th>Public Key</th>
                <th>Allowed IPs</th>
                <th>Endpoint</th>
                <th>Last Contact</th>
                <th>Transfer</th>
            </tr>
        """

        for p in peers:
            html += f"""
            <tr>
                <td style="font-family: monospace;">{p.get("public_key")}</td>
                <td>{p.get("allowed ips", "—")}</td>
                <td>{p.get("endpoint", "—")}</td>
                <td>{parse_handshake(p.get("latest handshake", "—"))}</td>
                <td>{p.get("transfer", "—").replace("received", "⬇").replace("sent", "⬆")}</td>
            </tr>
            """

        html += "</table>"

    html += "</body></html>"
    return HTMLResponse(content=html)


class LocationUpdateRequest(BaseModel):
    wg_ip: str
    latitude: float
    longitude: float


@app.post("/set-peer_location")
def set_client_location(
    req: LocationUpdateRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Find peer by IP
            cur.execute(
                "SELECT id FROM sensos.wireguard_peers WHERE wg_ip = %s;",
                (req.wg_ip,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Peer not found.")

            peer_id = row[0]
            cur.execute(
                """
                INSERT INTO sensos.peer_locations (peer_id, location)
                VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326));
                """,
                (peer_id, req.longitude, req.latitude),  # Note: lon, lat order!
            )
            conn.commit()

    return {"status": "location stored"}


@app.get("/get-peer_location")
def get_client_location(
    wg_ip: str,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.recorded_at, ST_Y(l.location)::float AS latitude, ST_X(l.location)::float AS longitude
                FROM sensos.peer_locations l
                JOIN sensos.wireguard_peers p ON l.peer_id = p.id
                WHERE p.wg_ip = %s
                ORDER BY l.recorded_at DESC
                LIMIT 1;
                """,
                (wg_ip,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No location found.")

            return {
                "latitude": row[1],
                "longitude": row[2],
                "recorded_at": row[0],
            }
