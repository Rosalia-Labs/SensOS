import os
import subprocess
import logging
import psycopg
import docker
import time
import re

from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse, HTMLResponse
from psycopg.errors import UniqueViolation


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
    """Ensure the `sensos` schema and networks/devices tables exist before running queries."""
    logger.info("Initializing database schema and tables...")

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Ensure the schema exists
                logger.info("Creating schema 'sensos' if not exists...")
                cur.execute("CREATE SCHEMA IF NOT EXISTS sensos;")

                # Create the `networks` table inside `sensos`
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

                # Create the `devices` table inside `sensos`
                logger.info("Creating table 'sensos.devices' if not exists...")
                cur.execute(
                    """
                CREATE TABLE IF NOT EXISTS sensos.devices (
                    id SERIAL PRIMARY KEY,
                    network_id INTEGER REFERENCES sensos.networks(id) ON DELETE CASCADE,
                    hostname TEXT UNIQUE NOT NULL,
                    wg_ip INET UNIQUE NOT NULL,
                    wg_public_key TEXT UNIQUE NOT NULL
                );
                """
                )

        logger.info("Database schema initialization complete.")

    except Exception as e:
        logger.error(f"Error initializing database: {e}", exc_info=True)


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

    match = re.search(
        r"\[Interface\].*?PrivateKey\s*=\s*(\S+).*?\n\n", config, re.DOTALL
    )
    if not match:
        raise ValueError(
            f"PrivateKey not found in {wg_config_path}. Check the file format."
        )

    return match.group(0)  # Return the whole `[Interface]` section


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

                # Collect all clients for this network
                cur.execute(
                    """
                    SELECT wg_ip, wg_public_key FROM sensos.devices WHERE network_id = %s;
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

    logger.info("WireGuard configuration regenerated for all networks.")


@app.post("/register-client")
def register_client(network_name: str = Form(...), wg_public_key: str = Form(...)):
    """Registers a new client and assigns it an IP address in the network."""
    logger.info(
        f"Received request for network: {network_name}, Public Key: {wg_public_key}"
    )

    with get_db() as conn:
        with conn.cursor() as cur:
            # Ensure the network exists
            cur.execute(
                "SELECT id, ip_range, wg_public_key FROM sensos.networks WHERE name = %s;",
                (network_name,),
            )
            network = cur.fetchone()

            if not network:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Network '{network_name}' not found."},
                )

            network_id, ip_range, server_public_key = network

            # Ensure the public key is not already registered
            cur.execute(
                "SELECT wg_ip, hostname FROM sensos.devices WHERE wg_public_key = %s;",
                (wg_public_key,),
            )
            existing_device = cur.fetchone()

            if existing_device:
                existing_ip, existing_hostname = existing_device
                return JSONResponse(
                    status_code=409,  # HTTP 409 Conflict
                    content={
                        "error": "Device already registered",
                        "wg_ip": existing_ip,
                        "hostname": existing_hostname,
                        "server_public_key": server_public_key,
                    },
                )

            # Generate a new IP address (same logic as before)
            base_ip = ".".join(str(ip_range.network_address).split(".")[:2])
            cur.execute(
                "SELECT wg_ip FROM sensos.devices WHERE network_id = %s ORDER BY wg_ip DESC LIMIT 1;",
                (network_id,),
            )
            last_ip = cur.fetchone()

            if last_ip and last_ip[0]:
                last_ip_parts = str(last_ip[0]).split(".")
                third_octet = int(last_ip_parts[-2])
                fourth_octet = int(last_ip_parts[-1])

                if fourth_octet < 254:
                    new_third_octet = third_octet
                    new_fourth_octet = fourth_octet + 1
                else:
                    new_third_octet = third_octet + 1
                    new_fourth_octet = 1
            else:
                new_third_octet = 1
                new_fourth_octet = 1

            if new_third_octet > 255:
                return JSONResponse(
                    status_code=400, content={"error": "No available IP addresses"}
                )

            new_ip = f"{base_ip}.{new_third_octet}.{new_fourth_octet}"
            hostname = f"{network_name}-{new_third_octet}-{new_fourth_octet}"

            # Insert the new device
            try:
                cur.execute(
                    """
                    INSERT INTO sensos.devices (network_id, hostname, wg_ip, wg_public_key)
                    VALUES (%s, %s, %s, %s);
                """,
                    (network_id, hostname, new_ip, wg_public_key),
                )
            except UniqueViolation:
                return JSONResponse(
                    status_code=409,
                    content={"error": "Device with this public key already exists."},
                )

    regenerate_wireguard_config()
    restart_wireguard_container()

    return {
        "wg_ip": new_ip,
        "hostname": hostname,
        "server_public_key": server_public_key,
    }
