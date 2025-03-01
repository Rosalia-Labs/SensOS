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
import re

from fastapi import FastAPI, Depends, HTTPException, Form
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg.errors import UniqueViolation
from typing import Tuple, Optional
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WG_CONFIG_DIR = Path("/config/wg_confs")
CONTROLLER_CONFIG_DIR = Path("/etc/wireguard")

app = FastAPI()
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

# Registry values
SENSOS_REGISTRY_IP = os.getenv("SENSOS_REGISTRY_IP", "127.0.0.1")
SENSOS_REGISTRY_USER = os.getenv("SENSOS_REGISTRY_USER", "sensos")
SENSOS_REGISTRY_PORT = os.getenv("SENSOS_REGISTRY_PORT", "5000")

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


def get_last_assigned_ip(network_id: int) -> Optional[str]:
    """Fetch the highest assigned IP address for the given network."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wg_ip FROM sensos.wireguard_peers WHERE network_id = %s ORDER BY wg_ip DESC LIMIT 1;",
                (network_id,),
            )
            result = cur.fetchone()
            return result[0] if result else None


def compute_next_ip(ip_range, last_ip, subnet_offset=0):
    """Compute the next available IP address in a specified subnetwork."""
    network = ipaddress.ip_network(ip_range, strict=False)

    # Compute the base IP for this subnetwork (x.x.<subnet_offset>.1)
    base_ip_parts = list(map(int, str(network.network_address).split(".")))
    base_ip_parts[2] = subnet_offset  # Set the third octet to the desired subnetwork
    base_ip_parts[3] = 1  # Start from x.x.<subnet_offset>.1

    # If no last IP, return the first address in the subnetwork
    if not last_ip:
        return ".".join(map(str, base_ip_parts))

    # Increment the last assigned IP
    last_ip_parts = list(map(int, str(last_ip).split(".")))

    if last_ip_parts[2] == subnet_offset and last_ip_parts[3] < 254:
        last_ip_parts[3] += 1
    elif last_ip_parts[2] < subnet_offset:
        last_ip_parts[2] = subnet_offset
        last_ip_parts[3] = 1
    else:
        return None  # No more available IPs in this subnet

    return ".".join(map(str, last_ip_parts))


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


def restart_wireguard_container():
    """Restarts the WireGuard container, supporting both Linux and Windows."""
    client = get_docker_client()
    container_name = "sensos-wireguard"

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


def start_wireguard(network_name: str):
    """Starts WireGuard for the given network if a valid configuration exists."""
    wg_config = CONTROLLER_CONFIG_DIR / f"{network_name}.conf"

    if not wg_config.exists():
        logger.warning(f"WireGuard config {wg_config} not found. Skipping start.")
        return

    try:
        subprocess.run(["wg-quick", "up", f"{network_name}"], check=True)
        logger.info(f"WireGuard started successfully for network {network_name}.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start WireGuard: {e}")


def start_controller_wireguard():
    """Scans /etc/wireguard for config files and brings them up one by one."""
    logger.info("🔍 Scanning /etc/wireguard for existing WireGuard configurations...")

    # Get all config files
    config_files = sorted(CONTROLLER_CONFIG_DIR.glob("*.conf"))

    if not config_files:
        logger.warning("⚠️ No WireGuard config files found in /etc/wireguard.")
        return

    for config_file in config_files:
        network_name = config_file.stem  # Extract network name from filename

        logger.info(f"🚀 Enabling WireGuard interface: {network_name}")
        try:
            subprocess.run(["wg-quick", "up", network_name], check=True)
            logger.info(
                f"✅ Successfully activated WireGuard interface: {network_name}"
            )
        except subprocess.CalledProcessError as e:
            logger.error(
                f"❌ Failed to activate WireGuard interface {network_name}: {e}"
            )

    logger.info("✅ All available WireGuard interfaces have been enabled.")


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


def insert_peer(network_id: int, wg_ip: str) -> int:
    """Insert a new peer into the database and return its ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sensos.wireguard_peers (network_id, wg_ip) VALUES (%s, %s) RETURNING id;",
                (network_id, wg_ip),
            )
            return cur.fetchone()[0]


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


def create_wireguard_configs(
    network_id: int, name: str, ip_range: str, private_key: str, wg_public_key: str
):
    """Creates WireGuard configuration files for both the WireGuard container and the controller."""

    # Define paths
    wg_config_path = WG_CONFIG_DIR / f"{name}.conf"
    controller_config_path = CONTROLLER_CONFIG_DIR / f"{name}.conf"

    # Assign fixed IPs
    base_ip = ip_range.split("/")[0]  # Get the base IP without CIDR
    network_prefix = ".".join(base_ip.split(".")[:3])  # Extract "x.x.0"
    controller_ip = f"{network_prefix}.1"  # Controller gets x.x.0.1
    wireguard_ip = f"{network_prefix}.2"  # WireGuard gets x.x.0.2

    # Generate keys for the controller itself
    controller_private_key, controller_public_key = generate_wireguard_keys()

    insert_peer(network_id, controller_ip)
    register_wireguard_key_in_db(controller_ip, controller_public_key)

    insert_peer(network_id, wireguard_ip)
    register_wireguard_key_in_db(wireguard_ip, wg_public_key)
    wireguard_container_ip = get_container_ip("sensos-wireguard")

    # WireGuard container config (main server)
    wg_config_content = f"""[Interface]
Address = {wireguard_ip}/16
ListenPort = 51820
PrivateKey = {private_key}

[Peer]
PublicKey = {controller_public_key}
AllowedIPs = {controller_ip}/32
"""

    # Controller config (acts as a peer)
    controller_config_content = f"""[Interface]
Address = {controller_ip}
PrivateKey = {controller_private_key}

[Peer]
PublicKey = {wg_public_key}
AllowedIPs = {ip_range}
Endpoint = {wireguard_container_ip}:51820
PersistentKeepalive = 25
"""

    # Write WireGuard container config
    with open(wg_config_path, "w") as f:
        f.write(wg_config_content)
    os.chmod(wg_config_path, stat.S_IRUSR | stat.S_IWUSR)

    # Write Controller config
    logger.info(f"Attempting to write WireGuard config to {controller_config_path}")

    with open(controller_config_path, "w") as f:
        f.write(controller_config_content)
    os.chmod(controller_config_path, stat.S_IRUSR | stat.S_IWUSR)

    logger.info(f"Successfully wrote WireGuard config to {controller_config_path}")

    return wg_config_path, controller_config_path


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


def create_config_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def update_config_table(cur):
    cur.execute(
        """
        INSERT INTO sensos.config (key, value) VALUES
            ('registry_ip', %s),
            ('registry_port', %s),
            ('registry_user', %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
        """,
        (SENSOS_REGISTRY_IP, SENSOS_REGISTRY_PORT, SENSOS_REGISTRY_USER),
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
            wg_port INTEGER UNIQUE NOT NULL CHECK (wg_port > 0 AND wg_port <= 65535),
            wg_public_key TEXT UNIQUE NOT NULL
        );
        """
    )


def create_wireguard_peers_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensos.wireguard_peers (
            id SERIAL PRIMARY KEY,
            network_id INTEGER REFERENCES sensos.networks(id) ON DELETE CASCADE,
            wg_ip INET UNIQUE NOT NULL
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


def ensure_network_exists(cur):
    network_name = os.getenv("INITIAL_NETWORK")
    if not network_name:
        logger.error("❌ INITIAL_NETWORK is not set in .env. Exiting.")
        return None

    logger.info(f"🔍 Checking if network '{network_name}' exists...")
    cur.execute(
        """
        SELECT id, ip_range, wg_public_ip, wg_port, wg_public_key 
        FROM sensos.networks WHERE name = %s;
        """,
        (network_name,),
    )
    existing_network = cur.fetchone()

    if existing_network:
        network_id, ip_range, wg_public_ip, wg_port, wg_public_key = existing_network
        logger.info(f"✅ Network '{network_name}' already exists.")
        if not wg_public_ip or not wg_port:
            logger.warning(
                f"⚠️ Updating missing WireGuard IP/port for '{network_name}'..."
            )
            wg_public_ip = os.getenv("WG_IP", "127.0.0.1")
            wg_port = int(os.getenv("WG_PORT", "51820"))
            cur.execute(
                """
                UPDATE sensos.networks 
                SET wg_public_ip = %s, wg_port = %s 
                WHERE id = %s;
                """,
                (wg_public_ip, wg_port, network_id),
            )
            logger.info(
                f"✅ Updated '{network_name}' with WireGuard IP {wg_public_ip} and port {wg_port}."
            )
    else:
        logger.info(f"🆕 Creating new network '{network_name}'...")
        ip_range = generate_default_ip_range(network_name)
        wg_public_ip = os.getenv("WG_IP", "127.0.0.1")
        wg_port = int(os.getenv("WG_PORT", "51820"))
        private_key, public_key = generate_wireguard_keys()
        cur.execute(
            """
            INSERT INTO sensos.networks (name, ip_range, wg_public_ip, wg_port, wg_public_key)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (network_name, ip_range, wg_public_ip, wg_port, public_key),
        )
        network_id = cur.fetchone()[0]
        logger.info(f"✅ Created new network '{network_name}' (ID: {network_id}).")
        create_wireguard_configs(
            network_id, network_name, ip_range, private_key, public_key
        )
    return network_id


@app.on_event("startup")
def bootstrap():
    """Ensure the database schema, tables, and network exist at startup."""
    logger.info("Initializing database schema and tables...")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                logger.info("Creating schema 'sensos' if not exists...")
                cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")
                create_config_table(cur)
                update_config_table(cur)
                create_version_history_table(cur)
                update_version_history_table(cur)
                create_networks_table(cur)
                create_wireguard_peers_table(cur)
                create_wireguard_keys_table(cur)
                create_ssh_keys_table(cur)
                network_id = ensure_network_exists(cur)
                if network_id:
                    add_peers_to_wireguard()
                    restart_wireguard_container()
                    start_controller_wireguard()
                    logger.info("✅ WireGuard setup completed.")
        logger.info("✅ Database schema and tables initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}", exc_info=True)


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
    credentials: HTTPBasicCredentials = Depends(authenticate),
    name: str = Form(...),
    wg_public_ip: Optional[str] = Form(None),
    wg_port: Optional[str] = Form(None),  # Accept as string to validate manually
):
    """Creates a new sensor network, sets up WireGuard, and saves it to PostgreSQL."""

    # Use environment variables if not provided
    wg_public_ip = wg_public_ip or os.getenv("WG_IP", "127.0.0.1")

    # Ensure wg_port is a valid integer
    try:
        wg_port = int(wg_port) if wg_port else int(os.getenv("WG_PORT", 51820))
        if not (1 <= wg_port <= 65535):  # Valid port range check
            raise ValueError("Port must be between 1 and 65535.")
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid WireGuard port. Must be a number between 1 and 65535."
            },
        )

    ip_range = generate_default_ip_range(name)

    # Generate WireGuard key pair
    private_key, public_key = generate_wireguard_keys()

    with get_db() as conn:
        with conn.cursor() as cur:
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
            except psycopg.errors.UniqueViolation:
                return JSONResponse(
                    status_code=400, content={"error": "Network already exists"}
                )

    # Pass network_id to create_wireguard_configs
    create_wireguard_configs(network_id, name, ip_range, private_key, public_key)

    add_peers_to_wireguard()
    restart_wireguard_container()

    # Start WireGuard after config is created
    start_wireguard(name)

    return {
        "id": network_id,
        "name": name,
        "ip_range": ip_range,
        "wg_public_ip": wg_public_ip,
        "wg_port": wg_port,
        "wg_public_key": public_key,
    }


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
    subnet_offset: int = 0  # New: Start from x.x.<subnet_offset>.1


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

    insert_peer(network_id, wg_ip)

    return {
        "wg_ip": wg_ip,
        "wg_public_key": public_key,
        "wg_public_ip": wg_public_ip,
        "wg_port": wg_port,
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
    restart_wireguard_container()

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


@app.get("/get-registry-info")
def get_registry_info(credentials: HTTPBasicCredentials = Depends(authenticate)):
    """
    Retrieves the Sensos Registry connection details from the database:
      - registry_ip: The IP address for the registry container.
      - registry_port: The port on which the registry is listening.
      - registry_user: The username for registry authentication.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, value FROM sensos.config WHERE key IN ('registry_ip', 'registry_port', 'registry_user');"
            )
            rows = cur.fetchall()
            config = {key: value for key, value in rows}
    return {
        "registry_ip": config.get("registry_ip", SENSOS_REGISTRY_IP),
        "registry_port": config.get("registry_port", SENSOS_REGISTRY_PORT),
        "registry_user": config.get("registry_user", SENSOS_REGISTRY_USER),
    }
