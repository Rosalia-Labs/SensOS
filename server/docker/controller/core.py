# core.py
import os
import stat
import ipaddress
import logging
import psycopg
import socket
import docker
import time
import tempfile

from psycopg import Cursor

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Tuple, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from wireguard import (
    WireGuardService,
    WireGuardInterface,
    WireGuardInterfaceEntry,
    WireGuardPeerEntry,
    WireGuard,
)

# ------------------------------------------------------------
# Logging & Configuration
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

CONTROLLER_CONFIG_DIR = Path("/etc/wireguard")
API_PROXY_CONFIG_DIR = Path("/api_proxy_config")
WG_CONFIG_DIR = Path("/wireguard_config")

# ensure dirs
API_PROXY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
WG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

wg = WireGuard()
wgs = WireGuardService()


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
                generate_api_proxy_wireguard_configs(cur, False)
                generate_controller_wireguard_configs(cur)
                create_initial_network(cur)
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


def generate_default_ip_range(name: str) -> ipaddress.IPv4Network:
    hash_val = sum(ord(c) for c in name) % 256
    return ipaddress.ip_network(f"10.{hash_val}.0.0/16")


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
    Registers a WireGuard public key in the database for an existing peer,
    deactivating any previous keys for that peer.

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

            # Deactivate all existing keys for this peer
            cur.execute(
                "UPDATE sensos.wireguard_keys SET is_active = FALSE WHERE peer_id = %s;",
                (peer_id,),
            )

            # Insert the new key
            cur.execute(
                "INSERT INTO sensos.wireguard_keys (peer_id, wg_public_key, is_active) VALUES (%s, %s, TRUE);",
                (peer_id, wg_public_key),
            )

    return {"wg_ip": wg_ip, "wg_public_key": wg_public_key}


def create_network_entry(
    cur: Cursor,
    name: str,
    wg_public_ip: str,
    wg_port: int,
) -> dict:
    """
    Creates a new network entry in the DB, plus on-disk WireGuard config.
    If a network with this name already exists, returns its details immediately.
    """
    cur.execute(
        """
        SELECT id, ip_range, wg_public_ip, wg_port, wg_public_key
        FROM sensos.networks
        WHERE name = %s;
        """,
        (name,),
    )
    existing = cur.fetchone()
    if existing:
        return {
            "id": existing[0],
            "name": name,
            "ip_range": existing[1],
            "wg_public_ip": existing[2],
            "wg_port": existing[3],
            "wg_public_key": existing[4],
        }

    ip_range = generate_default_ip_range(name)

    wg_iface = WireGuardInterface(name=name, config_dir=WG_CONFIG_DIR)
    wg_iface.ensure_directories()

    private_key = wg.genkey()
    wg_iface.set_interface(
        WireGuardInterfaceEntry(
            address=wg_public_ip,
            private_key=private_key,
            listen_port=wg_port,
        )
    )

    wg_iface.interface_entry.validate()

    wg_iface.save_config(overwrite=True)

    public_key = wg.pubkey(private_key)

    cur.execute(
        """
        INSERT INTO sensos.networks
          (name, ip_range, wg_public_ip, wg_port, wg_public_key)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (name, ip_range, wg_public_ip, wg_port, public_key),
    )
    network_id = cur.fetchone()[0]

    generate_api_proxy_wireguard_configs(cur)
    generate_controller_wireguard_configs(cur)
    generate_wireguard_container_configs(cur)

    return {
        "id": network_id,
        "name": name,
        "ip_range": ip_range,
        "wg_public_ip": wg_public_ip,
        "wg_port": wg_port,
        "wg_public_key": public_key,
    }


def update_wireguard_configs():
    with get_db() as conn:
        with conn.cursor() as cur:
            generate_wireguard_container_configs(cur)


def generate_api_proxy_wireguard_configs(
    cur: Cursor,
    restart_api_proxy_container: bool = True,
) -> None:
    """
    For each network in the DB, ensure a valid WireGuard config exists for the API proxy container.
    - On-disk private keys are never overwritten.
    - New keys are only generated by WireGuardInterface.set_interface().
    - Registers the new public key in the DB exactly once.
    """

    cur.execute(
        """
        SELECT id, name, ip_range, wg_public_key, wg_port
        FROM sensos.networks;
        """
    )
    networks = cur.fetchall()

    for network_id, name, ip_range_cidr, server_pub_key, wg_port in networks:
        ip_range = ipaddress.ip_network(ip_range_cidr, strict=False)
        proxy_ip = ip_range.network_address + 1
        proxy_ip_str = str(proxy_ip)

        iface = WireGuardInterface(name=name, config_dir=API_PROXY_CONFIG_DIR)
        priv_key = None
        if iface.config_exists():
            iface.load_config()
            priv_key = iface.get_private_key()

        iface.set_interface(
            WireGuardInterfaceEntry(
                address=f"{proxy_ip_str}/32",
                listen_port=wg_port,
                private_key=priv_key,
            )
        )

        iface.peer_defs.clear()
        iface.add_peer(
            WireGuardPeerEntry(
                PublicKey=server_pub_key,
                Endpoint=f"sensos-wireguard:{wg_port}",
                AllowedIPs=str(ip_range),
                PersistentKeepalive="25",
            )
        )

        iface.save_config(overwrite=True)

        cur.execute(
            "SELECT 1 FROM sensos.wireguard_peers WHERE network_id = %s AND wg_ip = %s",
            (network_id, proxy_ip_str),
        )
        if cur.fetchone() is None:
            insert_peer(network_id, proxy_ip_str, note="API Proxy Container")
            register_wireguard_key_in_db(
                proxy_ip_str, wg.pubkey(iface.get_private_key())
            )

    if restart_api_proxy_container:
        restart_container("sensos-api-proxy")
    logger.info("✅ Reconciled API proxy configs for all networks.")


def generate_controller_wireguard_configs(
    cur: Cursor,
) -> None:
    """
    For each network in the DB, ensure a valid WireGuard config exists for the API proxy container.
    - On-disk private keys are never overwritten.
    - New keys are only generated by WireGuardInterface.set_interface().
    - Registers the new public key in the DB exactly once.
    """

    cur.execute(
        """
        SELECT id, name, ip_range, wg_public_key, wg_port
        FROM sensos.networks;
        """
    )
    networks = cur.fetchall()

    for network_id, name, ip_range_cidr, server_pub_key, wg_port in networks:
        ip_range = ipaddress.ip_network(ip_range_cidr, strict=False)
        controller_ip = ip_range.network_address + 2
        controller_ip_str = str(controller_ip)

        iface = WireGuardInterface(name=name, config_dir=WG_CONFIG_DIR)
        priv_key = None
        if iface.config_exists():
            iface.load_config()
            priv_key = iface.get_private_key()

        iface.set_interface(
            WireGuardInterfaceEntry(
                address=f"{controller_ip_str}/32",
                private_key=priv_key,
            )
        )

        iface.peer_defs.clear()
        iface.add_peer(
            WireGuardPeerEntry(
                PublicKey=server_pub_key,
                Endpoint=f"sensos-wireguard:{wg_port}",
                AllowedIPs=str(ip_range),
                PersistentKeepalive="25",
            )
        )

        iface.save_config(overwrite=True)

        cur.execute(
            "SELECT 1 FROM sensos.wireguard_peers WHERE network_id = %s AND wg_ip = %s",
            (network_id, controller_ip_str),
        )
        if cur.fetchone() is None:
            insert_peer(network_id, controller_ip_str, note="Controller Container")
            register_wireguard_key_in_db(
                controller_ip_str, wg.pubkey(iface.get_private_key())
            )

        wgs.bring_up(name)

    logger.info("✅ Reconciled controller configs for all networks.")


def generate_wireguard_container_configs(
    cur: Cursor, restart_wireguard_container: bool = True
) -> None:
    """
    For each network in the DB, ensure the WireGuard server interface is
    configured with all peers (clients + api-proxy), but do not register
    anything new here—you’ve already registered api-proxy in the DB.
    """
    # 1) fetch all networks
    cur.execute("SELECT id, name, ip_range, wg_port FROM sensos.networks;")
    networks = cur.fetchall()

    for network_id, name, ip_range_cidr, wg_port in networks:

        iface = WireGuardInterface(name=name, config_dir=WG_CONFIG_DIR)
        if not iface.config_exists():
            raise RuntimeError(f"Missing WireGuard config for network '{name}'")
        iface.load_config()
        try:
            priv_key = iface.get_private_key()
        except Exception as e:
            raise RuntimeError(f"Incomplete config for network '{name}': {e}")

        iface.set_interface(
            WireGuardInterfaceEntry(
                private_key=priv_key,
                listen_port=wg_port,
            )
        )
        iface.interface_entry.validate()

        # 4) rebuild peers list from the DB (clients + api-proxy)
        cur.execute(
            """
            SELECT p.wg_ip, k.wg_public_key
              FROM sensos.wireguard_peers p
              JOIN sensos.wireguard_keys k
                ON p.id = k.peer_id
             WHERE p.network_id = %s AND k.is_active = TRUE;
            """,
            (network_id,),
        )
        iface.peer_entries = [
            WireGuardPeerEntry(PublicKey=pub, AllowedIPs=f"{ip}/32")
            for ip, pub in cur.fetchall()
        ]

        # 5) write the .conf
        iface.save_config(overwrite=True)

    # 6) restart once, if desired
    if restart_wireguard_container:
        restart_container("sensos-wireguard")
    logger.info("✅ Reconciled WireGuard configs for all networks.")


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

    used_ips.add(ip_range.network_address + 1)
    used_ips.add(ip_range.network_address + 2)

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
