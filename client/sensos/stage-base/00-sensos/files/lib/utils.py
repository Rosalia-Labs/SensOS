import os
import sys
import pwd
import grp
import stat
import shlex
import shutil
import base64
import requests
import tempfile
import subprocess
import configparser
import argparse
import json

API_PASSWORD_FILE = "/sensos/keys/api_password"
DEFAULTS_CONF = "/sensos/etc/defaults.conf"
NETWORK_CONF = "/sensos/etc/network.conf"
DEFAULT_PORT = "8765"


def privileged_shell(cmd, check=False, silent=False, user=None):
    """
    Run a shell command. If user is specified, always use sudo -u <user>.
    If not, try direct, then retry with sudo on failure.
    Returns (output, rc).
    """
    if user:
        sudo_cmd = f"sudo -u {user} {cmd}"
        try:
            output = subprocess.check_output(sudo_cmd, shell=True, text=True).strip()
            return output, 0
        except subprocess.CalledProcessError as e:
            if not silent:
                print(f"❌ Sudo command failed: {sudo_cmd}\n{e}", file=sys.stderr)
            if check:
                raise
            return None, e.returncode
        except Exception as e:
            if not silent:
                print(f"❌ Error running {sudo_cmd}: {e}", file=sys.stderr)
            if check:
                raise
            return None, 1
    else:
        try:
            output = subprocess.check_output(cmd, shell=True, text=True).strip()
            return output, 0
        except subprocess.CalledProcessError as e:
            sudo_cmd = f"sudo {cmd}"
            try:
                output = subprocess.check_output(
                    sudo_cmd, shell=True, text=True
                ).strip()
                return output, 0
            except subprocess.CalledProcessError as se:
                if not silent:
                    print(f"❌ Sudo command failed: {sudo_cmd}\n{se}", file=sys.stderr)
                if check:
                    raise
                return None, se.returncode
        except Exception as e:
            if not silent:
                print(f"❌ Error running {cmd}: {e}", file=sys.stderr)
            if check:
                raise
            return None, 1


def remove_dir(path):
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except PermissionError:
        privileged_shell(f"rm -rf {shlex.quote(path)}", silent=True)


def create_dir(path, owner="root", mode=0o700):
    try:
        os.makedirs(path, exist_ok=True)
        os.chmod(path, mode)
        uid = pwd.getpwnam(owner).pw_uid
        gid = grp.getgrnam(owner).gr_gid
        os.chown(path, uid, gid)
    except PermissionError:
        privileged_shell(f"mkdir -p {shlex.quote(path)}", silent=True)
        privileged_shell(f"chmod {oct(mode)[2:]} {shlex.quote(path)}", silent=True)
        privileged_shell(f"chown {owner}:{owner} {shlex.quote(path)}", silent=True)


def remove_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except PermissionError:
        privileged_shell(f"rm -f {shlex.quote(path)}", silent=True)


def any_files_in_dir(path):
    try:
        return len(os.listdir(path)) > 0
    except PermissionError:
        output, rc = privileged_shell(f"ls -A {shlex.quote(path)}", silent=True)
        return bool(output and output.strip())
    except FileNotFoundError:
        return False


def read_file(filepath):
    try:
        with open(filepath, "r") as f:
            return f.read().strip()
    except PermissionError:
        output, rc = privileged_shell(f"cat {shlex.quote(filepath)}", silent=True)
        return output.strip() if output else None
    except Exception as e:
        print(f"❌ Error reading file {filepath}: {e}", file=sys.stderr)
        return None


def write_file(filepath, content, mode=0o644, user="root", group=None):
    # Try Python first, escalate if necessary
    group = group or user
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            shutil.move(tmp_path, filepath)
        except PermissionError:
            privileged_shell(
                f"mv {shlex.quote(tmp_path)} {shlex.quote(filepath)}", silent=True
            )
            tmp_path = None  # It's moved already
        try:
            os.chmod(filepath, mode)
        except PermissionError:
            privileged_shell(
                f"chmod {oct(mode)[2:]} {shlex.quote(filepath)}", silent=True
            )
        try:
            uid = pwd.getpwnam(user).pw_uid
            gid = grp.getgrnam(group).gr_gid
            os.chown(filepath, uid, gid)
        except PermissionError:
            privileged_shell(
                f"chown {user}:{group} {shlex.quote(filepath)}", silent=True
            )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ----- Rest: unchanged logic -----


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
    return read_file(API_PASSWORD_FILE)


def detect_wireguard_api():
    if not os.path.exists(NETWORK_CONF):
        return None, None
    config = {}
    with open(NETWORK_CONF) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                config[k] = v
    port = config.get("SERVER_PORT", DEFAULT_PORT)
    server_ip = config.get("SERVER_WG_IP")
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
    def check_server_reachable():
        try:
            url = f"http://{config_server}:{port}/"
            response = requests.get(url, timeout=3)
            return True  # Server is up
        except requests.exceptions.ConnectionError:
            return False
        except Exception as e:
            print(
                f"⚠️ Unexpected error when checking server availability: {e}",
                file=sys.stderr,
            )
            return False

    if not check_server_reachable():
        print(
            f"❌ Cannot reach configuration server at {config_server}:{port}.",
            file=sys.stderr,
        )
        print("📡 Is the device online? Is the server address correct?")
        return None
    tries = 3
    for attempt in range(tries):
        if os.path.exists(API_PASSWORD_FILE):
            stored_password = read_file(API_PASSWORD_FILE)
            print("Testing stored API password...")
            if validate_api_password(config_server, port, stored_password):
                print("✅ API password from file is valid.")
                return stored_password
            else:
                print("⚠️ Stored API password is invalid.", file=sys.stderr)
        api_password = input("🔑 Enter API password: ").strip()
        if validate_api_password(config_server, port, api_password):
            if not api_password:
                print("❌ Error: API password is empty. Not saving.", file=sys.stderr)
                continue
            write_file(API_PASSWORD_FILE, api_password + "\n", mode=0o640, user="root")
            print(f"✅ API password saved securely in {API_PASSWORD_FILE}.")
            return api_password
        else:
            print("❌ API password is invalid, please try again.", file=sys.stderr)
    print(
        "🚫 Failed to provide a valid API password after 3 attempts.", file=sys.stderr
    )
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


def read_kv_config(path):
    config = {}
    if not os.path.exists(path):
        return config
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            config[key.strip()] = val.strip()
    return config


def get_client_wg_ip():
    """Return CLIENT_WG_IP from /sensos/etc/network.conf, or None if not found."""
    config = read_kv_config(NETWORK_CONF)
    return config.get("CLIENT_WG_IP")


def set_permissions_and_owner(
    path: str, mode: int, user: str = None, group: str = None
):
    """Set file permissions and ownership. Falls back to sudo on permission error."""
    group = group or user
    try:
        os.chmod(path, mode)
    except PermissionError:
        privileged_shell(f"chmod {oct(mode)[2:]} {shlex.quote(path)}", silent=True)
    if user:
        try:
            uid = pwd.getpwnam(user).pw_uid
            gid = grp.getgrnam(group).gr_gid
            os.chown(path, uid, gid)
        except PermissionError:
            privileged_shell(f"chown {user}:{group} {shlex.quote(path)}", silent=True)


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
