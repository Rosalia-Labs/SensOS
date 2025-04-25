import os
import stat
import shutil
import subprocess
from pathlib import Path


class WireGuardError(Exception):
    """Base class for WireGuard errors."""


class WireGuardPermissionError(WireGuardError):
    """Raised when a WireGuard operation requires root privileges."""


class WireGuard:
    def __init__(self, wg_binary: str = "wg"):
        self.wg_binary = wg_binary
        if not shutil.which(self.wg_binary):
            raise FileNotFoundError(
                f"WireGuard binary '{self.wg_binary}' not found in PATH"
            )

    def _run(self, *args: str, input_text: str = None) -> str:
        try:
            result = subprocess.run(
                [self.wg_binary, *args],
                input=input_text,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            if "Permission denied" in e.stderr:
                raise WireGuardPermissionError(
                    f"Permission denied when running: {self.wg_binary} {' '.join(args)}"
                ) from e
            raise

    def genkey(self) -> str:
        return self._run("genkey")

    def genpsk(self) -> str:
        return self._run("genpsk")

    def pubkey(self, private_key: str) -> str:
        return self._run("pubkey", input_text=private_key)

    def show(self, interface: str = None) -> str:
        if interface:
            return self._run("show", interface)
        return self._run("show")

    def showconf(self, interface: str) -> str:
        return self._run("showconf", interface)

    def set(self, interface: str, *args: str) -> None:
        self._run("set", interface, *args)

    def setconf(self, interface: str, config_file: Path) -> None:
        self._run("setconf", interface, str(config_file))

    def addconf(self, interface: str, config_file: Path) -> None:
        self._run("addconf", interface, str(config_file))

    def syncconf(self, interface: str, config_file: Path) -> None:
        self._run("syncconf", interface, str(config_file))


class WireGuardPrivateKeyFile:
    def __init__(self, path: Path):
        self.path = path
        self.wg = WireGuard()

    def exists(self) -> bool:
        return self.path.exists()

    def generate(self, overwrite: bool = False) -> None:
        if self.exists() and not overwrite:
            raise FileExistsError(f"Private key already exists at {self.path}")
        private_key = self.wg.genkey()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(private_key)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def read(self) -> str:
        return self.path.read_text().strip()

    def public_key(self) -> str:
        return self.wg.pubkey(self.read())

    def validate_public_key(self, public_key: str) -> bool:
        return public_key == self.public_key()


class WireGuardPeer:
    def __init__(self, **fields):
        self.fields = fields

    @classmethod
    def from_lines(cls, lines: list[str]) -> "WireGuardPeer":
        fields = {}
        for line in lines:
            if "=" in line:
                key, value = map(str.strip, line.split("=", 1))
                fields[key] = value
        return cls(**fields)

    def to_lines(self) -> list[str]:
        return [f"{key} = {value}" for key, value in self.fields.items()]

    @property
    def public_key(self) -> str:
        return self.fields.get("PublicKey")

    @property
    def allowed_ips(self) -> str:
        return self.fields.get("AllowedIPs")

    @property
    def endpoint(self) -> str:
        return self.fields.get("Endpoint")

    @property
    def persistent_keepalive(self) -> str:
        return self.fields.get("PersistentKeepalive")

    @public_key.setter
    def public_key(self, value: str) -> None:
        self.fields["PublicKey"] = value

    @allowed_ips.setter
    def allowed_ips(self, value: str) -> None:
        self.fields["AllowedIPs"] = value

    @endpoint.setter
    def endpoint(self, value: str) -> None:
        self.fields["Endpoint"] = value

    @persistent_keepalive.setter
    def persistent_keepalive(self, value: str) -> None:
        self.fields["PersistentKeepalive"] = value

    def __repr__(self) -> str:
        important_fields = []
        for key in ["PublicKey", "AllowedIPs", "Endpoint"]:
            if key in self.fields:
                important_fields.append(f"{key}={self.fields[key]}")
        if not important_fields:
            important_fields = [f"{k}={v}" for k, v in self.fields.items()]
        return f"WireGuardPeer({', '.join(important_fields)})"

    def validate(self) -> None:
        """Validates that the peer has all required fields.

        Raises:
            ValueError if required fields are missing.
        """
        missing = []
        for required_field in ["PublicKey", "AllowedIPs"]:
            if required_field not in self.fields:
                missing.append(required_field)

        if missing:
            raise ValueError(f"Missing required peer field(s): {', '.join(missing)}")


class WireGuardInterfaceConfigFile:
    def __init__(self, path: Path):
        self.path = path
        self.interface_settings: dict[str, str] = {}
        self.peers: list[WireGuardPeer] = []

    def exists(self) -> bool:
        return self.path.exists()

    def set_interface(
        self, private_key: str, address: str, listen_port: int, **extra_options
    ) -> None:
        """Sets [Interface] block. Private key must be provided."""
        self.interface_settings = {
            "PrivateKey": private_key,
            "Address": address,
            "ListenPort": str(listen_port),
        }
        for key, value in extra_options.items():
            if key in self.interface_settings:
                raise ValueError(f"Cannot override {key}")
            self.interface_settings[key] = value

    def add_peer(self, peer: WireGuardPeer) -> None:
        self.peers.append(peer)

    def remove_peer(self, peer: WireGuardPeer) -> None:
        self.peers.remove(peer)

    def render_config(self) -> str:
        lines = ["[Interface]"]
        for key, value in self.interface_settings.items():
            lines.append(f"{key} = {value}")
        lines.append("")  # blank line

        for peer in self.peers:
            lines.append("[Peer]")
            lines.extend(peer.to_lines())
            lines.append("")  # blank line

        return "\n".join(lines)

    def save(self) -> None:
        config_text = self.render_config()
        self.path.write_text(config_text)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def load(self) -> None:
        """Loads existing config from disk into memory."""
        if not self.exists():
            raise FileNotFoundError(f"Config file {self.path} does not exist.")

        self.interface_settings.clear()
        self.peers.clear()

        with self.path.open("r") as f:
            lines = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]

        current_section = None
        current_fields = []

        for line in lines:
            if line == "[Interface]":
                if current_section == "Peer" and current_fields:
                    self.peers.append(WireGuardPeer.from_lines(current_fields))
                current_section = "Interface"
                current_fields = []
            elif line == "[Peer]":
                if current_section == "Interface" and current_fields:
                    for l in current_fields:
                        key, value = map(str.strip, l.split("=", 1))
                        self.interface_settings[key] = value
                elif current_section == "Peer" and current_fields:
                    self.peers.append(WireGuardPeer.from_lines(current_fields))
                current_section = "Peer"
                current_fields = []
            else:
                current_fields.append(line)

        if current_section == "Interface" and current_fields:
            for l in current_fields:
                key, value = map(str.strip, l.split("=", 1))
                self.interface_settings[key] = value
        elif current_section == "Peer" and current_fields:
            self.peers.append(WireGuardPeer.from_lines(current_fields))


class WireGuardInterface:
    def __init__(self, name: str, config_dir: Path = Path("/etc/wireguard")):
        self.name = name
        self.config_dir = config_dir
        self.wg = WireGuard()

    @property
    def config_file(self) -> Path:
        return self.config_dir / f"{self.name}.conf"

    @property
    def key_dir(self) -> Path:
        return self.config_dir / "keys"

    @property
    def private_key_file(self) -> Path:
        return self.key_dir / f"{self.name}.privatekey"

    def interface_path(self) -> str:
        """Returns config file path as string, for use by wg-quick."""
        return str(self.config_file)

    def config_exists(self) -> bool:
        return self.config_file.exists()

    def key_exist(self) -> bool:
        return self.private_key_file.exists()

    def ensure_directories(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.key_dir.mkdir(parents=True, exist_ok=True)

    def generate_key(self, overwrite: bool = False) -> None:
        """Generates private and public keys.

        Args:
            wg: WireGuard instance used to generate keys.
            overwrite: If True, will overwrite existing private key.

        Raises:
            FileExistsError: If private key exists and overwrite is False.
        """
        self.ensure_directories()

        if self.private_key_file.exists() and not overwrite:
            raise FileExistsError(
                f"Private key already exists for {self.name}; refusing to overwrite."
            )

        private_key = self.wg.genkey()
        self.private_key_file.write_text(private_key)
        os.chmod(self.private_key_file, stat.S_IRUSR | stat.S_IWUSR)

    def get_private_key(self) -> str:
        return self.private_key_file.read_text().strip()

    def get_public_key(self) -> str:
        return self.wg.pubkey(self.get_private_key())

    def get_keys(self) -> tuple[str, str]:
        """Reads and returns (private_key, public_key)."""
        private_key = self.get_private_key()
        public_key = self.get_public_key()
        return private_key, public_key

    def validate_publickey(self, testkey: str) -> bool:
        """Returns True if the test key matches the private key."""
        return testkey == self.get_public_key()

    def ensure_key_exist(self, wg: WireGuard) -> None:
        """Ensures private key exist.

        - If private key is missing, generates a new key.
        """
        self.ensure_directories()

        if not self.private_key_file.exists():
            self.generate_keys(wg)


class WireGuardConfiguration:
    def __init__(self, config_dir: Path = Path("/etc/wireguard")):
        self.config_dir = config_dir

    def interfaces(self) -> list[WireGuardInterface]:
        return [
            WireGuardInterface(p.stem, self.config_dir)
            for p in self.config_dir.glob("*.conf")
        ]

    def get_interface(self, name: str) -> WireGuardInterface:
        return WireGuardInterface(name, self.config_dir)

    def remove_interface(self, name: str) -> None:
        iface = self.get_interface(name)
        if iface.config_file.exists():
            iface.config_file.unlink()
        if iface.key_dir.exists():
            shutil.rmtree(iface.key_dir)

    def create_interface(
        self,
        name: str,
        wg: WireGuard,
        address: str,
        listen_port: int,
        save_config: bool = True,
        overwrite: bool = False,
    ) -> WireGuardInterface:
        """Creates a new interface with generated keys and minimal config.

        Args:
            name: Interface name (e.g. 'wg0')
            wg: WireGuard wrapper instance
            address: Interface IP address, e.g. '10.0.0.1/24'
            listen_port: Port to listen on
            save_config: Whether to write the config immediately
            overwrite: Allow overwriting existing keys/configs (default False)

        Raises:
            FileExistsError: If private key, public key, or config already exists and overwrite=False
        """
        iface = self.get_interface(name)

        iface.ensure_directories()

        # Check for existing keys
        if iface.private_key_file.exists() or iface.public_key_file.exists():
            if not overwrite:
                raise FileExistsError(
                    f"Keys already exist for {name}. Use overwrite=True to replace them."
                )

        # Check for existing config
        if iface.config_file.exists():
            if not overwrite:
                raise FileExistsError(
                    f"Config already exists for {name}. Use overwrite=True to replace it."
                )

        # Generate fresh keys
        iface.generate_keys(wg)

        private_key = iface.read_private_key()

        config_lines = [
            "[Interface]",
            f"Address = {address}",
            f"ListenPort = {listen_port}",
            f"PrivateKey = {private_key}",
            "",
        ]

        if save_config:
            iface.config_file.write_text("\n".join(config_lines))
            os.chmod(iface.config_file, stat.S_IRUSR | stat.S_IWUSR)

        return iface


class WireGuardQuick:
    def __init__(self, wg_quick_binary: str = "wg-quick"):
        self.wg_quick_binary = wg_quick_binary
        if not shutil.which(self.wg_quick_binary):
            raise FileNotFoundError(
                f"WireGuard Quick binary '{self.wg_quick_binary}' not found in PATH"
            )

    def up(self, interface: WireGuardInterface) -> None:
        subprocess.run(
            [self.wg_quick_binary, "up", interface.interface_path()],
            check=True,
        )

    def down(self, interface: WireGuardInterface) -> None:
        subprocess.run(
            [self.wg_quick_binary, "down", interface.interface_path()],
            check=True,
        )

    def save(self, interface: WireGuardInterface) -> None:
        subprocess.run(
            [self.wg_quick_binary, "save", interface.interface_path()],
            check=True,
        )

    def strip(self, interface: WireGuardInterface) -> str:
        result = subprocess.run(
            [self.wg_quick_binary, "strip", interface.interface_path()],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


class WireGuardService:
    def __init__(self, config_dir: Path = Path("/etc/wireguard")):
        self.config_dir = config_dir
        self.quick = WireGuardQuick()
        self.wg = WireGuard()

    def list_interfaces(self) -> list[str]:
        """Return a list of interface names based on config files present."""
        return [p.stem for p in self.config_dir.glob("*.conf") if p.is_file()]

    def get_interface(self, name: str) -> WireGuardInterface:
        return WireGuardInterface(name, self.config_dir)

    def interfaces(self) -> list[WireGuardInterface]:
        return [self.get_interface(name) for name in self.list_interfaces()]

    def bring_up(self, name: str) -> None:
        iface = self.get_interface(name)
        self.quick.up(iface)

    def bring_down(self, name: str) -> None:
        iface = self.get_interface(name)
        self.quick.down(iface)

    def bring_all_up(self) -> None:
        for iface in self.interfaces():
            self.quick.up(iface)

    def bring_all_down(self) -> None:
        for iface in self.interfaces():
            self.quick.down(iface)
