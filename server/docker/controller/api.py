# api.py
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import ipaddress

from fastapi import (
    APIRouter,
    Depends,
    BackgroundTasks,
    Form,
    HTTPException,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasicCredentials


from core import (
    get_db,
    insert_peer,
    authenticate,
    get_network_details,
    search_for_next_available_ip,
    register_wireguard_key_in_db,
    update_wireguard_configs,
    create_network_entry,
    lookup_client_id,
)

from models import (
    RegisterPeerRequest,
    RegisterWireguardKeyRequest,
    RegisterSSHKeyRequest,
    LocationUpdateRequest,
    ClientStatusRequest,
    HardwareProfile,
)

logger = logging.getLogger(__name__)
router = APIRouter()


WG_STATUS_DIR = Path("/wireguard_config")


@router.get("/", response_class=HTMLResponse)
def dashboard(credentials: HTTPBasicCredentials = Depends(authenticate)):
    """
    Display a dashboard with network version and status information.
    Uses get_db() from core to fetch the latest version info and list networks.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM sensos.version_history ORDER BY timestamp DESC LIMIT 1;"
            )
            version_info = cur.fetchone()

            cur.execute(
                "SELECT name, ip_range, wg_public_ip, wg_port FROM sensos.networks ORDER BY name;"
            )
            networks = cur.fetchall()

    if networks:
        network_table = (
            "<h3>🌐 Registered Networks</h3><table>"
            "<tr><th>Network Name</th><th>IP Range</th><th>Public IP</th><th>Port</th></tr>"
        )
        for network in networks:
            network_table += (
                f"<tr><td>{network[0]}</td><td>{network[1]}</td>"
                f"<td>{network[2]}</td><td>{network[3]}</td></tr>"
            )
        network_table += "</table>"
    else:
        network_table = "<p style='color: red;'>⚠️ No registered networks found.</p>"

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

    html_content = f"""
    <html>
    <head>
        <title>Sensor Network Manager</title>
        <style>
            /* CSS styling here */
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
    return html_content


@router.post("/create-network")
def create_network(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(authenticate),
    name: str = Form(...),
    wg_public_ip: str = Form(...),
    wg_port: str = Form(...),
):
    try:
        wg_port = int(wg_port)
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

        return result

    except RuntimeError as e:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": str(e)},
        )


@router.get("/list-peers", response_class=HTMLResponse)
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


@router.post("/register-peer")
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

    wg_ip = search_for_next_available_ip(
        subnet, network_id, start_third_octet=request.subnet_offset
    )

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


@router.post("/register-wireguard-key")
def register_wireguard_key(
    request: RegisterWireguardKeyRequest,
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Endpoint that registers a WireGuard key for an existing peer."""
    result = register_wireguard_key_in_db(request.wg_ip, request.wg_public_key)

    if result is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Peer '{request.wg_ip}' not found."},
        )

    background_tasks.add_task(update_wireguard_configs)

    return result


@router.post("/exchange-ssh-keys")
def exchange_ssh_keys(
    request: RegisterSSHKeyRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Registers an SSH public key for a peer."""
    with get_db() as conn:
        with conn.cursor() as cur:
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

            network_id, peer_id = result

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

            inserted_key = cur.fetchone()

            if not inserted_key:
                raise HTTPException(
                    status_code=409, detail="SSH key already exists for this peer."
                )

        conn.commit()

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


@router.get("/inspect-database", response_class=HTMLResponse)
def inspect_database(
    limit: int = 10,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """Inspect all database tables in a single formatted HTML output."""
    with get_db() as conn:
        with conn.cursor() as cur:
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


@router.get("/get-peer-info")
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


@router.post("/client-status")
def client_status(
    status: ClientStatusRequest,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    with get_db() as conn:
        client_id = lookup_client_id(conn, status.wireguard_ip)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sensos.client_status (
                    client_id, last_check_in, hostname, uptime_seconds,
                    disk_available_gb, memory_used_mb, memory_total_mb,
                    load_1m, load_5m, load_15m, version, status_message
                ) VALUES (
                    %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    client_id,
                    status.hostname,
                    status.uptime_seconds,
                    status.disk_available_gb,
                    status.memory_used_mb,
                    status.memory_total_mb,
                    status.load_1m,
                    status.load_5m,
                    status.load_15m,
                    status.version,
                    status.status_message,
                ),
            )
            conn.commit()
    return {"message": "Client status updated successfully"}


@router.get("/get-wireguard-network-names")
def get_defined_networks():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM sensos.networks;")
            network_names = [row[0] for row in cur.fetchall()]
    return {"networks": network_names}


@router.get("/get-network-info")
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


@router.post("/upload-hardware-profile")
def upload_hardware_profile(
    profile: HardwareProfile,
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    profile_data = profile.model_dump()
    wg_ip = profile_data.pop("wg_ip", None)
    if not wg_ip:
        raise HTTPException(status_code=400, detail="wg_ip must be provided.")
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


@router.get("/wireguard-status", response_class=HTMLResponse)
def wireguard_status_dashboard(
    credentials: HTTPBasicCredentials = Depends(authenticate),
):
    """
    Displays an HTML dashboard showing WireGuard peer status for all active interfaces.
    Falls back to a warning if no status files are found.
    """
    status_files = sorted(WG_STATUS_DIR.glob("wireguard_status_*.txt"))
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


@router.post("/set-peer-location")
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
                (peer_id, req.longitude, req.latitude),
            )
            conn.commit()

    return {"status": "location stored"}


@router.get("/get-peer_location")
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
