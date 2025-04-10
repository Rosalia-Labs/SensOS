import os
import sys
import stat
import base64
import subprocess
import requests
import configparser
import argparse

API_PASSWORD_FILE = "/sensos/.sensos_api_password"
DEFAULTS_CONF = "/sensos/etc/defaults.conf"
NETWORK_CONF = "/sensos/etc/network.conf"
DEFAULT_PORT = "8765"


def run_command(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_basic_auth(api_password):
    return base64.b64encode(f":{api_password}".encode()).decode()


def remove_nulls(data):
    if isinstance(data, dict):
        return {k: remove_nulls(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [remove_nulls(item) for item in data]
    elif isinstance(data, str):
        return data.replace("\x00", "")
    return data


def read_api_password():
    if not os.path.exists(API_PASSWORD_FILE):
        print("❌ API password file missing", file=sys.stderr)
        return None
    with open(API_PASSWORD_FILE) as f:
        return f.read().strip()


def detect_wireguard_api():
    if not os.path.exists(NETWORK_CONF):
        return None, None

    config = {}
    with open(NETWORK_CONF) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                config[k] = v

    server_ip = config.get("SERVER_IP")
    port = config.get("SERVER_PORT", DEFAULT_PORT)

    url = f"http://{server_ip}:{port}/"
    try:
        resp = requests.get(url, timeout=2)
        if resp.ok:
            return server_ip, port
    except Exception:
        pass

    return None, None


def load_defaults(*sections, path=DEFAULTS_CONF):
    defaults = {}
    if not os.path.exists(path):
        return defaults

    parser = configparser.ConfigParser()
    parser.optionxform = str  # preserve case
    parser.read(path)

    for section in sections:
        if section in parser:
            defaults.update(parser[section].items())
    return defaults


def parse_args_with_defaults(arg_defs, default_sections):
    defaults = load_defaults(*default_sections)
    parser = argparse.ArgumentParser()
    for args, kwargs in arg_defs:
        default_key = kwargs.get("dest", args[0].lstrip("-").replace("-", "_"))
        if default_key in defaults:
            kwargs["default"] = defaults[default_key]
        parser.add_argument(*args, **kwargs)
    return parser.parse_args()


def read_network_conf():
    config = {}
    if not os.path.exists(NETWORK_CONF):
        print(f"❌ {NETWORK_CONF} not found", file=sys.stderr)
        return {}
    with open(NETWORK_CONF) as f:
        for line in f:
            if "=" in line:
                key, val = line.strip().split("=", 1)
                config[key.strip()] = val.strip()
    return config


def validate_api_password(config_server, port, api_password):
    url = f"http://{config_server}:{port}/"
    headers = {"Authorization": f"Basic {get_basic_auth(api_password)}"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error testing API password: {e}", file=sys.stderr)
        return False


def get_api_password(config_server, port):
    tries = 3
    for attempt in range(tries):
        if os.path.exists(API_PASSWORD_FILE):
            with open(API_PASSWORD_FILE, "r") as f:
                stored_password = f.read().strip()
            print("Testing stored API password...")
            if validate_api_password(config_server, port, stored_password):
                print("✅ API password from file is valid.")
                return stored_password
            else:
                print("⚠️ Stored API password is invalid.", file=sys.stderr)
        api_password = input("🔑 Enter API password: ").strip()
        if validate_api_password(config_server, port, api_password):
            if api_password is None or api_password == "":
                print("❌ Error: API password is empty. Not saving.", file=sys.stderr)
                continue
            with open(API_PASSWORD_FILE, "w") as f:
                f.write(api_password)
            os.chmod(API_PASSWORD_FILE, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            print(f"✅ API password saved securely in {API_PASSWORD_FILE}.")
            return api_password
        else:
            print("❌ API password is invalid, please try again.", file=sys.stderr)
    print(
        "🚫 Failed to provide a valid API password after 3 attempts.", file=sys.stderr
    )
    return None


def read_file(filepath):
    try:
        with open(filepath, "r") as f:
            return f.read().strip()
    except Exception as e:
        print(f"❌ Error reading file {filepath}: {e}", file=sys.stderr)
        return None


def sudo_read_file(filepath):
    try:
        content = run_command(f"sudo cat {filepath}")
        return content.strip()
    except Exception as e:
        print(f"❌ Error reading {filepath} with sudo: {e}", file=sys.stderr)
        return None


def compute_api_server_wg_ip(client_wg_ip):
    parts = client_wg_ip.split(".")
    if len(parts) != 4:
        print(
            f"❌ Error: Invalid client WireGuard IP format: {client_wg_ip}",
            file=sys.stderr,
        )
        return None
    return f"{parts[0]}.{parts[1]}.0.1"


def parse_local_config(config_file):
    config = {}
    content = sudo_read_file(config_file)
    if not content:
        return config
    current_section = None
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
        elif "=" in line and current_section:
            key, value = [x.strip() for x in line.split("=", 1)]
            if current_section.lower() == "interface" and key.lower() == "address":
                config["wg_ip"] = value.split("/")[0]
            elif current_section.lower() == "peer" and key.lower() == "publickey":
                config["server_pubkey"] = value
    return config


def compute_hostname(network_name, wg_ip):
    """Compute hostname as {network_name}-{3rd_octet}-{4th_octet} from a WG IP like '10.1.3.7'."""
    ip_parts = wg_ip.split(".")
    if len(ip_parts) != 4:
        print(f"❌ Error: Invalid IP format '{wg_ip}'.", file=sys.stderr)
        return None
    return f"{network_name}-{ip_parts[2]}-{ip_parts[3]}"


class Tee:
    """Write output to both terminal and a log file."""

    def __init__(self, log_file, mode="a"):
        self.terminal = sys.stdout
        self.log = open(log_file, mode)

    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def setup_logging(log_filename=None):
    """Configure logging to /sensos/log/{script_name}.log or a custom filename, plus stdout/stderr."""

    script_name = os.path.basename(sys.argv[0])
    if "." in script_name:
        script_name = script_name.split(".")[0]

    if log_filename is None:
        log_filename = f"{script_name}.log"

    log_dir = "/sensos/log"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, log_filename)

    sys.stdout = Tee(log_path)
    sys.stderr = sys.stdout


# Note: the registry functionality is not being used at the moment

REGISTRY_CONFIG_FILE = "/sensos/.sensos_registry_config.json"


def validate_registry_password(registry_info, registry_password):
    url = f"https://{registry_info['registry_ip']}:{registry_info['registry_port']}/v2/"
    auth = (registry_info["registry_user"], registry_password)
    try:
        response = requests.get(url, auth=auth, timeout=5, verify=False)
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        print(f"❌ Debug: Error during request: {e}", file=sys.stderr)
        return False


def save_registry_config(registry_info, registry_password):
    if not registry_info or not registry_password:
        print(
            "❌ Error: Incomplete registry configuration data. Not saving.",
            file=sys.stderr,
        )
        return
    required_keys = ["registry_ip", "registry_port", "registry_user"]
    if not all(key in registry_info for key in required_keys):
        print(
            "❌ Error: Registry information is incomplete. Not saving.", file=sys.stderr
        )
        return
    config = {
        "registry_ip": registry_info["registry_ip"],
        "registry_port": registry_info["registry_port"],
        "registry_user": registry_info["registry_user"],
        "registry_password": registry_password,
    }
    with open(REGISTRY_CONFIG_FILE, "w") as f:
        json.dump(config, f)
    os.chmod(REGISTRY_CONFIG_FILE, 0o640)
    print(f"✅ Registry configuration saved to {REGISTRY_CONFIG_FILE}.")


def get_registry_password_from_info(registry_info):
    tries = 3
    for attempt in range(tries):
        if os.path.exists(REGISTRY_CONFIG_FILE):
            with open(REGISTRY_CONFIG_FILE, "r") as f:
                stored_config = json.load(f)
                stored_password = stored_config.get("registry_password")
                if stored_password:
                    return stored_password
            print(
                "⚠️ Password not found in config file or file malformed.",
                file=sys.stderr,
            )

        registry_password = input("🔑 Enter registry password: ").strip()
        if not registry_password:
            print("❌ Error: Empty registry password provided.", file=sys.stderr)
            continue
        if validate_registry_password(registry_info, registry_password):
            save_registry_config(registry_info, registry_password)
            print(f"✅ Registry password saved to {REGISTRY_CONFIG_FILE}.")
            return registry_password
    print(
        "❌ Failed to obtain a valid registry password after 3 attempts.",
        file=sys.stderr,
    )
    return None

def enable_service(service_name, start=False):
    """Enable and optionally start the specified systemd service using sudo."""
    if shutil.which("systemctl") is None:
        print(
            f"❌ Error: systemctl command not found. Skipping enabling service {service_name}.",
            file=sys.stderr,
        )
        return
    try:
        subprocess.run(["sudo", "systemctl", "enable", service_name], check=True)
        if start:
            subprocess.run(["sudo", "systemctl", "start", service_name], check=True)
            print(f"✅ Service {service_name} enabled and started.")
        else:
            print(f"✅ Service {service_name} enabled.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error enabling service {service_name}: {e}", file=sys.stderr)


def safe_cmd_output(cmd, try_sudo=False):
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except subprocess.CalledProcessError as e:
        if not try_sudo:
            return safe_cmd_output(f"sudo {cmd}", try_sudo=True)
        else:
            return f"ERROR: {e}"
