import os
import stat
import subprocess
import ipaddress
import logging
import psycopg
import docker
import time
import re

from fastapi import FastAPI, Form
from typing import Tuple, Optional
from fastapi.responses import JSONResponse, HTMLResponse
from psycopg.errors import UniqueViolation
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WG_CONFIG_DIR = "/etc/wireguard/wg_confs/"

app = FastAPI()

POSTGRES_USER = "postgres"
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@sensos-database/postgres"
)

if not POSTGRES_PASSWORD:
    raise ValueError("POSTGRES_PASSWORD is not set. Exiting.")


def restart_wireguard_container():
    """Restarts the WireGuard container using Docker API."""
    client = docker.DockerClient(base_url="unix://var/run/docker.sock")
    try:
        container = client.containers.get("wireguard")
        container.restart()
        logger.info("WireGuard container restarted successfully.")
    except Exception as e:
        logger.info(f"Error restarting WireGuard container: {e}")


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


@app.on_event("startup")
def init_db():
    """Ensure the `sensos` schema and networks/devices/tables exist before running queries."""
    logger.info("Initializing database schema and tables...")

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Ensure the schema exists
                logger.info("Creating schema 'sensos' if not exists...")
                cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")

                # Create the `networks` table
                logger.info("Creating table 'sensos.networks' if not exists...")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sensos.networks (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        ip_range CIDR UNIQUE NOT NULL,
                        wg_public_key TEXT UNIQUE NOT NULL
                    );
                    """
                )

                # Create the `devices` table (no more wg_public_key here)
                logger.info("Creating table 'sensos.devices' if not exists...")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sensos.devices (
                        id SERIAL PRIMARY KEY,
                        network_id INTEGER REFERENCES sensos.networks(id) ON DELETE CASCADE,
                        wg_ip INET UNIQUE NOT NULL
                    );
                    """
                )

                # Create the `wireguard_keys` table (supports multiple keys per device)
                logger.info("Creating table 'sensos.wireguard_keys' if not exists...")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sensos.wireguard_keys (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER REFERENCES sensos.devices(id) ON DELETE CASCADE,
                        wg_public_key TEXT UNIQUE NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                    """
                )

                # Create the `ssh_keys` table (supports multiple keys per device)
                logger.info("Creating table 'sensos.ssh_keys' if not exists...")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sensos.ssh_keys (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER REFERENCES sensos.devices(id) ON DELETE CASCADE,
                        ssh_public_key TEXT UNIQUE NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                    """
                )

        logger.info("✅ Database schema initialization complete.")

        regenerate_wireguard_config()
        restart_wireguard_container()

        logger.info(
            "✅ Regenerated wireguard configs and restarted wireguard container."
        )

    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}", exc_info=True)


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Render a simple form to create a network."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, ip_range FROM sensos.networks;")
            networks = cur.fetchall()

    network_list = (
        "<br>".join(f"<b>{name}</b>: {ip_range}" for name, ip_range in networks)
        or "No networks yet."
    )

    return f"""
    <html>
    <head><title>Sensor Network Manager</title></head>
    <body>
        <h2>Create a New Sensor Network</h2>
        <form action="/create-network" method="post">
            <label for="name">Network Name:</label>
            <input type="text" id="name" name="name" required>
            <button type="submit">Create Network</button>
        </form>
        <h3>Existing Networks</h3>
        {network_list}
    </body>
    </html>
    """


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


@app.post("/create-network")
def create_network(name: str = Form(...)):
    """Creates a new sensor network, sets up WireGuard, and saves it to PostgreSQL."""

    ip_range = generate_default_ip_range(name)

    # Generate WireGuard key pair
    private_key, public_key = generate_wireguard_keys()

    with get_db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO sensos.networks (name, ip_range, wg_public_key)
                    VALUES (%s, %s, %s)
                    RETURNING id;
                """,
                    (name, ip_range, public_key),
                )
                network_id = cur.fetchone()[0]
            except psycopg.errors.UniqueViolation:
                return JSONResponse(
                    status_code=400, content={"error": "Network already exists"}
                )

    # Create WireGuard config file
    wg_config_path = f"{WG_CONFIG_DIR}{name}.conf"
    with open(wg_config_path, "w") as f:
        f.write(
            f"""[Interface]
Address = {ip_range.split('/')[0]}/16
ListenPort = 51820
PrivateKey = {private_key}

"""
        )

    restart_wireguard_container()

    return {
        "id": network_id,
        "name": name,
        "ip_range": ip_range,
        "wg_public_key": public_key,
    }


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


def regenerate_wireguard_config():
    """Regenerates all WireGuard configuration files based on the database."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, ip_range, wg_public_key FROM sensos.networks;"
            )
            networks = cur.fetchall()

            for network_id, network_name, ip_range, server_public_key in networks:
                wg_config_path = f"{WG_CONFIG_DIR}{network_name}.conf"

                # Extract existing server config to preserve the PrivateKey
                server_config = extract_server_config(wg_config_path)

                # Collect all active clients and their WireGuard keys for this network
                cur.execute(
                    """
                    SELECT d.wg_ip, k.wg_public_key 
                    FROM sensos.devices d
                    JOIN sensos.wireguard_keys k ON d.id = k.device_id
                    WHERE d.network_id = %s AND k.is_active = TRUE;
                    """,
                    (network_id,),
                )
                clients = cur.fetchall()

                # Write the new config
                with open(wg_config_path, "w") as f:
                    f.write(server_config)

                    for wg_ip, wg_public_key in clients:
                        f.write(
                            f"""
[Peer]
PublicKey = {wg_public_key}
AllowedIPs = {wg_ip}/32
"""
                        )

                # Set file permissions to 600 (owner read/write only)
                os.chmod(wg_config_path, stat.S_IRUSR | stat.S_IWUSR)

    logger.info(
        "✅ WireGuard configuration regenerated for all networks with secure permissions."
    )


@app.get("/list-clients", response_class=HTMLResponse)
def list_clients():
    """Displays a web page listing all registered clients."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.wg_ip, n.name AS network_name
                FROM sensos.devices d
                JOIN sensos.networks n ON d.network_id = n.id
                ORDER BY n.name, d.wg_ip;
                """
            )
            clients = cur.fetchall()

    # Generate an HTML table
    client_table = """
    <html>
    <head>
        <title>Registered Clients</title>
        <style>
            body { font-family: Arial, sans-serif; }
            table { width: 80%%; border-collapse: collapse; margin: 20px auto; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h2 style="text-align: center;">Registered Clients</h2>
        <table>
            <tr>
                <th>WireGuard IP</th>
                <th>Network Name</th>
            </tr>
    """

    for row in clients:
        wg_ip, network_name = row
        client_table += f"""
        <tr>
            <td>{wg_ip}</td>
            <td>{network_name}</td>
        </tr>
        """

    client_table += """
        </table>
    </body>
    </html>
    """

    return HTMLResponse(content=client_table)


def get_network_details(network_name: str):
    """Fetch the network ID, IP range, and public key for the given network name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ip_range, wg_public_key FROM sensos.networks WHERE name = %s;",
                (network_name,),
            )
            return cur.fetchone()


def get_last_assigned_ip(network_id: int) -> Optional[str]:
    """Fetch the highest assigned IP address for the given network."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wg_ip FROM sensos.devices WHERE network_id = %s ORDER BY wg_ip DESC LIMIT 1;",
                (network_id,),
            )
            result = cur.fetchone()
            return result[0] if result else None


def compute_next_ip(ip_range, last_ip):
    """Compute the next available IP address"""
    network = ipaddress.ip_network(ip_range, strict=False)
    base_ip = str(network.network_address)  # Extract the first address in the subnet

    if last_ip:
        last_ip_str = str(last_ip)  # Convert IPv4Address to string
        last_ip_parts = list(map(int, last_ip_str.split(".")))
        if last_ip_parts[3] < 254:
            last_ip_parts[3] += 1
        else:
            last_ip_parts[3] = 1
            last_ip_parts[2] += 1
    else:
        last_ip_parts = list(map(int, base_ip.split(".")))  # Use base IP
        last_ip_parts[2] += 1  # Start at .1.1
        last_ip_parts[3] = 1

    wg_ip = ".".join(map(str, last_ip_parts))
    return wg_ip


def insert_device(network_id: int, wg_ip: str) -> int:
    """Insert a new device into the database and return its ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sensos.devices (network_id, wg_ip) VALUES (%s, %s) RETURNING id;",
                (network_id, wg_ip),
            )
            return cur.fetchone()[0]


class RegisterDeviceRequest(BaseModel):
    network_name: str


@app.post("/register-device")
def register_device(request: RegisterDeviceRequest):
    """Registers a new device, computes IP, and returns the network's public key."""
    network_details = get_network_details(request.network_name)
    if not network_details:
        return JSONResponse(
            status_code=404,
            content={"error": f"Network '{request.network_name}' not found."},
        )

    network_id, subnet, public_key = network_details
    last_ip = get_last_assigned_ip(network_id)
    wg_ip = compute_next_ip(subnet, last_ip)
    device_id = insert_device(network_id, wg_ip)

    return {
        "device_id": device_id,
        "wg_ip": wg_ip,
        "public_key": public_key,
    }


class RegisterWireguardKeyRequest(BaseModel):
    wg_ip: str
    wg_public_key: str


@app.post("/register-wireguard-key")
def register_wireguard_key(request: RegisterWireguardKeyRequest):
    """Registers a WireGuard key for an existing device."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM sensos.devices WHERE wg_ip = %s;",
                (request.wg_ip,),
            )
            device = cur.fetchone()
            if not device:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Device '{request.wg_ip}' not found."},
                )

            device_id = device[0]
            cur.execute(
                "INSERT INTO sensos.wireguard_keys (device_id, wg_public_key) VALUES (%s, %s);",
                (device_id, request.wg_public_key),
            )

    regenerate_wireguard_config()
    restart_wireguard_container()

    return {"wg_ip": request.wg_ip, "wg_public_key": request.wg_public_key}


class RegisterSSHKeyRequest(BaseModel):
    wg_ip: str
    ssh_public_key: str


@app.post("/register-ssh-key")
def register_ssh_key(request: RegisterSSHKeyRequest):
    """Registers an SSH key for an existing device."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM sensos.devices WHERE wg_ip = %s;",
                (request.wg_ip,),
            )
            device = cur.fetchone()
            if not device:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Device '{request.wg_ip}' not found."},
                )

            device_id = device[0]
            cur.execute(
                "INSERT INTO sensos.ssh_keys (device_id, ssh_public_key) VALUES (%s, %s);",
                (device_id, request.ssh_public_key),
            )

    return {"wg_ip": request.wg_ip, "ssh_public_key": request.ssh_public_key}
