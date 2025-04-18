# core.py
import os
import stat
import subprocess
import ipaddress
import logging
import psycopg
import socket
import docker
import time
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg.errors import UniqueViolation
from typing import Tuple, Optional
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
DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@sensos-database/postgres"
)

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
    """
    Async context manager for handling the application's startup and shutdown procedures.

    During startup, it:
      - Creates the 'sensos' schema if it doesn't exist.
      - Sets the search path to include the schema.
      - Creates and/or updates required database tables.
      - Initializes network configuration and WireGuard interfaces.

    During shutdown, it logs the shutdown procedure.

    Parameters:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None
    """
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
    """
    Verifies HTTP Basic credentials against the API_PASSWORD environment variable.

    Parameters:
        credentials (HTTPBasicCredentials): The credentials provided by the client.

    Returns:
        HTTPBasicCredentials: The same credentials if authentication is successful.

    Raises:
        HTTPException: If the provided password does not match the expected API_PASSWORD.
    """
    if credentials.password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials


# ------------------------------------------------------------
# Database Connection
# ------------------------------------------------------------
def get_db(retries: int = 10, delay: int = 3):
    """
    Establishes and returns a PostgreSQL database connection.

    The function will attempt to connect to the database for a specified number of times,
    with a delay between attempts, to handle potential startup race conditions.

    Parameters:
        retries (int): Number of connection attempts (default: 10).
        delay (int): Delay in seconds between attempts (default: 3).

    Returns:
        connection: A psycopg connection object with autocommit enabled.

    Raises:
        psycopg.OperationalError: If connection fails after all attempts.
    """
    for attempt in range(retries):
        try:
            return psycopg.connect(DATABASE_URL, autocommit=True)
        except psycopg.OperationalError:
            if attempt == retries - 1:
                raise
            logger.info(
                f"Database not ready, retrying in {delay} seconds... (Attempt {attempt+1}/{retries})"
            )
            time.sleep(delay)


# ------------------------------------------------------------
# Core Utility Functions
# ------------------------------------------------------------


def get_network_details(network_name: str):
    """
    Retrieves network details from the database based on the network name.

    Parameters:
        network_name (str): The name of the network.

    Returns:
        tuple or None: A tuple containing (id, ip_range, wg_public_key, wg_public_ip, wg_port)
                       if the network is found; otherwise, None.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ip_range, wg_public_key, wg_public_ip, wg_port
                FROM sensos.networks
                WHERE name = %s;
                """,
                (network_name,),
            )
            return cur.fetchone()


def restart_container(container_name: str):
    """
    Restarts a Docker container identified by its name.

    If the container is not running, logs a warning and attempts to restart it.

    Parameters:
        container_name (str): The name of the container to restart.

    Returns:
        None
    """
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        if container.status != "running":
            logger.warning(
                f"Container '{container_name}' is not running but will be restarted."
            )
        container.restart()
        logger.info(f"Container '{container_name}' restarted successfully.")
    except Exception as e:
        logger.error(f"Error restarting container '{container_name}': {e}")


def start_controller_wireguard():
    """
    Loads or updates WireGuard configurations from the controller configuration directory.

    Scans the /etc/wireguard directory for configuration files. For each file, it either:
      - Updates an already running WireGuard interface via wg-quick and wg syncconf.
      - Or brings up a new WireGuard interface using wg-quick up.

    Returns:
        None
    """
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
                subprocess.run(
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


def resolve_hostname(value: str):
    """
    Resolves a hostname or returns the value if it is already a valid IP address.

    Attempts to interpret the input as an IPv4 or IPv6 address. If not, performs a DNS
    lookup to resolve the hostname.

    Parameters:
        value (str): A hostname or IP address.

    Returns:
        str or None: The resolved IP address as a string, or None if resolution fails.
    """
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


def generate_default_ip_range(name: str):
    """
    Generates a default /16 CIDR IP range based on a deterministic hash of the network name.

    Parameters:
        name (str): The name of the network.

    Returns:
        str: A CIDR-formatted IP range string.
    """
    hash_val = sum(ord(c) for c in name) % 256
    return f"10.{hash_val}.0.0/16"


def generate_wireguard_keys():
    """
    Generates a WireGuard key pair using wg commands.

    Returns:
        tuple: A tuple (private_key, public_key), where each is a string.
    """
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
    """
    Inserts a new WireGuard peer entry into the database.

    Parameters:
        network_id (int): The ID of the network.
        wg_ip (str): The WireGuard IP to assign to the peer.
        note (str, optional): An optional note or description.

    Returns:
        tuple: A tuple containing the new peer's id and uuid.
    """
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
    """
    Registers a WireGuard public key in the database for an existing peer.

    Parameters:
        wg_ip (str): The WireGuard IP address of the peer.
        wg_public_key (str): The public key to register.

    Returns:
        dict or None: A dictionary containing the wg_ip and wg_public_key if successful,
                      otherwise None if the peer does not exist.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM sensos.wireguard_peers WHERE wg_ip = %s;", (wg_ip,)
            )
            peer = cur.fetchone()
            if not peer:
                return None
            peer_id = peer[0]
            cur.execute(
                "INSERT INTO sensos.wireguard_keys (peer_id, wg_public_key) VALUES (%s, %s);",
                (peer_id, wg_public_key),
            )
    return {"wg_ip": wg_ip, "wg_public_key": wg_public_key}


def create_network_entry(cur, name: str, wg_public_ip: str, wg_port):
    """
    Creates a new network entry in the database along with generating WireGuard keys
    and configurations.

    Parameters:
        cur: The database cursor.
        name (str): The name of the network.
        wg_public_ip (str): The public IP address for WireGuard.
        wg_port (int): The WireGuard port.

    Returns:
        dict: A dictionary containing the network details (id, name, ip_range, wg_public_ip, wg_port, wg_public_key).

    Raises:
        RuntimeError: If a unique constraint is violated and the existing network details conflict.
    """
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
        create_wireguard_configs(
            network_id, name, ip_range, private_key, public_key, wg_port
        )
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
        if result["wg_public_key"] != public_key:
            raise RuntimeError(
                f"❌ Refusing to overwrite existing WireGuard key for network '{name}'. "
                "Restore the original key or delete the network first."
            )
        logger.info(f"✅ Returning existing network: {result}")
        return result


def create_wireguard_configs(
    network_id: int,
    name: str,
    ip_range: str,
    private_key: str,
    wg_public_key: str,
    wg_port: int,
):
    """
    Generates and writes WireGuard configuration files for a network.

    Creates both server and, if enabled, controller configuration files.

    Parameters:
        network_id (int): The ID of the network.
        name (str): The name of the network.
        ip_range (str): The CIDR-formatted IP range of the network.
        private_key (str): The WireGuard private key for the server.
        wg_public_key (str): The WireGuard public key for the server.

    Returns:
        tuple: A tuple containing the path to the server config and the controller config
               (if EXPOSE_CONTAINERS is True), otherwise just the server config path.
    """
    WG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    wg_config_path = WG_CONFIG_DIR / f"{name}.conf"
    controller_config_path = CONTROLLER_CONFIG_DIR / f"{name}.conf"
    base_ip = ip_range.split("/")[0]
    network_prefix = ".".join(base_ip.split(".")[:3])
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
        wg_peers.append(
            f"\n[Peer]\nPublicKey = {controller_public_key}\nAllowedIPs = {controller_ip.split('/')[0]}/32\n"
        )
        wireguard_container_ip = get_container_ip("sensos-wireguard")
        controller_config_content = f"""[Interface]
Address = {controller_ip}
PrivateKey = {controller_private_key}

[Peer]
PublicKey = {wg_public_key}
AllowedIPs = {ip_range}
Endpoint = {wireguard_container_ip}:{wg_port}
PersistentKeepalive = 25
"""
        with open(controller_config_path, "w") as f:
            f.write(controller_config_content)
        os.chmod(controller_config_path, stat.S_IRUSR | stat.S_IWUSR)
        logger.info(f"✅ Controller WireGuard config written: {controller_config_path}")
    wg_config_content = f"""[Interface]
{"Address = " + wg_interface_ip if wg_interface_ip else ""}
ListenPort = {wg_port}
PrivateKey = {private_key}
""" + "".join(
        wg_peers
    )
    with open(wg_config_path, "w") as f:
        f.write(wg_config_content.strip() + "\n")
    os.chmod(wg_config_path, stat.S_IRUSR | stat.S_IWUSR)
    logger.info(f"✅ WireGuard server config written: {wg_config_path}")
    return wg_config_path, controller_config_path if EXPOSE_CONTAINERS else None


def get_container_ip(container_name: str):
    """
    Retrieves the IP address of a Docker container using the Docker SDK.

    Parameters:
        container_name (str): The name of the container.

    Returns:
        str or None: The container's IP address if found, otherwise None.

    Raises:
        ValueError: If no valid IP address is found.
    """
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        networks = container.attrs["NetworkSettings"]["Networks"]
        for network_name, network_info in networks.items():
            if "IPAddress" in network_info and network_info["IPAddress"]:
                return network_info["IPAddress"]
        raise ValueError(
            f"❌ No valid IP address found for container '{container_name}'"
        )
    except Exception as e:
        logger.error(f"❌ Error getting container IP for '{container_name}': {e}")
    return None


def load_private_key(wg_config_path: Path) -> str:
    """
    Loads the private key from an existing WireGuard config file.
    """
    if not wg_config_path.exists():
        raise FileNotFoundError(f"{wg_config_path} does not exist")

    with open(wg_config_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("PrivateKey ="):
                return line.split("=", 1)[1].strip()

    raise ValueError(f"PrivateKey not found in {wg_config_path}")


def add_peers_to_wireguard():
    """
    Regenerates the WireGuard configuration files based on the current database state.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, ip_range, wg_public_key, wg_port FROM sensos.networks;"
            )
            networks = cur.fetchall()
            for (
                network_id,
                network_name,
                ip_range,
                server_public_key,
                wg_port,
            ) in networks:
                wg_config_path = WG_CONFIG_DIR / f"{network_name}.conf"

                # Load private key from existing config
                private_key = load_private_key(wg_config_path)

                # Build fresh [Interface] config
                interface_config = f"""[Interface]
ListenPort = {wg_port}
PrivateKey = {private_key}
"""

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

                with open(wg_config_path, "w") as f:
                    f.write(interface_config.strip() + "\n\n")
                    for wg_ip, wg_public_key in peers:
                        f.write(
                            f"\n[Peer]\nPublicKey = {wg_public_key}\nAllowedIPs = {wg_ip}/32\n"
                        )
    logger.info("✅ WireGuard configuration regenerated for all networks.")


def get_assigned_ips(network_id: int) -> set[ipaddress.IPv4Address]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wg_ip FROM sensos.wireguard_peers WHERE network_id = %s;",
                (network_id,),
            )
            return {ipaddress.ip_address(row[0]) for row in cur.fetchall()}


def search_for_next_available_ip(
    network: str,
    network_id: int,
    start_third_octet: int = 0,
) -> Optional[ipaddress.IPv4Address]:
    """
    Finds the next available IP in the given network range, starting from start_third_octet.
    Walks through each /24 block (<prefix>.<third octet>.1–254) until an available IP is found.
    """
    ip_range = ipaddress.ip_network(network, strict=False)
    used_ips = get_assigned_ips(network_id)

    base_bytes = bytearray(ip_range.network_address.packed)
    max_subnet = ip_range.num_addresses // 256

    for third_octet in range(start_third_octet, max_subnet):
        base_bytes[2] = third_octet
        base_bytes[3] = 0
        subnet_base = ipaddress.IPv4Address(bytes(base_bytes))
        subnet_net = ipaddress.ip_network(f"{subnet_base}/24", strict=False)

        for host_ip in subnet_net.hosts():
            if host_ip not in used_ips:
                return host_ip

    return None


def create_version_history_table(cur):
    """
    Creates the version_history table to track version and Git information.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the networks table to store network configurations.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the wireguard_peers table to store peer information for WireGuard.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the wireguard_keys table to store WireGuard public keys for peers.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the ssh_keys table to store SSH key information associated with peers.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the client_status table to log periodic status information from clients.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Inserts a new version history record into the version_history table.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the hardware_profiles table to store hardware profile data for peers.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    Creates the peer_locations table to store geographical location data for peers.

    Parameters:
        cur: The database cursor.

    Returns:
        None
    """
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
    """
    If INITIAL_NETWORK is set, ensures the network exists.
    Does nothing if INITIAL_NETWORK is unset.

    Parameters:
        cur: The database cursor.

    Returns:
        int or None: The network ID if created or found, else None.
    """
    network_name = os.getenv("INITIAL_NETWORK")
    if not network_name:
        logger.info("🔵 INITIAL_NETWORK is not set. Skipping initial network creation.")
        return None

    cur.execute(
        "SELECT id FROM sensos.networks WHERE name = %s;",
        (network_name,),
    )
    existing_network = cur.fetchone()

    if existing_network:
        network_id = existing_network[0]
        logger.info(f"✅ Network '{network_name}' already exists (ID: {network_id}).")
        return network_id

    logger.info(f"📡 Network '{network_name}' not found. Creating...")

    wg_public_ip = os.getenv("WG_SERVER_IP")
    wg_port = os.getenv("WG_PORT")

    if not wg_public_ip or not wg_port:
        raise RuntimeError(
            f"❌ Cannot create network '{network_name}'. "
            "WG_SERVER_IP and WG_PORT must be set."
        )

    wg_port = int(wg_port)
    if not (1 <= wg_port <= 65535):
        raise RuntimeError(f"❌ Invalid WG_PORT: {wg_port}. Must be between 1–65535.")

    result = create_network_entry(cur, network_name, wg_public_ip, wg_port)
    logger.info(f"✅ Created network '{network_name}' (ID: {result['id']}).")
    return result["id"]
