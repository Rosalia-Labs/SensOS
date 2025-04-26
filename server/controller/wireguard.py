import os
import stat
import shutil
import subprocess
from pathlib import Path

INTERFACE_ALLOWED_FIELDS = {
    "PrivateKey",
    "Address",
    "ListenPort",
    "MTU",
    "DNS",
    "Table",
    "PreUp",
    "PostUp",
    "PreDown",
    "PostDown",
}

PEER_ALLOWED_FIELDS = {
    "PublicKey",
    "PresharedKey",
    "AllowedIPs",
    "Endpoint",
    "PersistentKeepalive",
}


def parse_sections(path: Path, strict: bool = True) -> dict[str, list[str]]:
    """Reads [section] blocks into a dict {header: lines}.

    Args:
        path: File to read.
        strict: If True, fail on lines outside any section.
                If False, skip lines outside sections.

    Returns:
        Dict mapping section headers to list of non-empty, non-comment lines.
    """
    sections = {}
    current_section = None
    current_lines = []

    with path.open("r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[") and line.endswith("]"):
                if current_section:
                    sections[current_section] = current_lines
                current_section = line
                current_lines = []
            elif current_section is None:
                if strict:
                    raise ValueError(f"Line outside any section: {line}")
                else:
                    continue
            else:
                current_lines.append(line)

        if current_section:
            sections[current_section] = current_lines

    return sections


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


class WireGuardPeerEntry:
    def __init__(self, **fields):
        self.fields = fields

    @classmethod
    def from_lines(cls, lines: list[str]) -> "WireGuardPeerEntry":
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
        return f"WireGuardPeerEntry({', '.join(important_fields)})"

    def validate(self) -> None:
        """Validate required fields and known keys."""
        for required in ("PublicKey", "AllowedIPs"):
            if required not in self.fields:
                raise ValueError(
                    f"Missing required field '{required}' in [Peer] section."
                )

        for key in self.fields:
            if key not in PEER_ALLOWED_FIELDS:
                raise ValueError(f"Unknown field '{key}' in [Peer] section.")


class WireGuardInterfaceEntry:
    def __init__(self, **fields):
        self.fields = fields

    @classmethod
    def from_lines(cls, lines: list[str]) -> "WireGuardInterfaceEntry":
        fields = {}
        for line in lines:
            if "=" in line:
                key, value = map(str.strip, line.split("=", 1))
                fields[key] = value
        return cls(**fields)

    def to_lines(self) -> list[str]:
        return [f"{key} = {value}" for key, value in self.fields.items()]

    @property
    def private_key(self) -> str:
        return self.fields.get("PrivateKey")

    @property
    def address(self) -> str:
        return self.fields.get("Address")

    @property
    def listen_port(self) -> str:
        return self.fields.get("ListenPort")

    def validate(self) -> None:
        """Validate required fields and known keys."""
        if "PrivateKey" not in self.fields:
            raise ValueError(
                "Missing required field 'PrivateKey' in [Interface] section."
            )

        for key in self.fields:
            if key not in INTERFACE_ALLOWED_FIELDS:
                raise ValueError(f"Unknown field '{key}' in [Interface] section.")


class WireGuardInterfaceConfigFile:
    def __init__(self, path: Path):
        self.path = path
        self.interface: WireGuardInterfaceEntry = None
        self.peers: list[WireGuardPeerEntry] = []

    def exists(self) -> bool:
        return self.path.exists()

    def set_interface(
        self, private_key: str, address: str, listen_port: int, **extra_options
    ) -> None:
        """Sets the [Interface] block."""
        self.interface = WireGuardInterfaceEntry(
            PrivateKey=private_key,
            Address=address,
            ListenPort=str(listen_port),
            **extra_options,
        )

    def add_peer(self, peer: WireGuardPeerEntry) -> None:
        self.peers.append(peer)

    def remove_peer(self, peer: WireGuardPeerEntry) -> None:
        self.peers.remove(peer)

    def render_config(self) -> str:
        if self.interface is None:
            raise ValueError("Interface config is not set.")

        lines = ["[Interface]"]
        lines.extend(self.interface.to_lines())
        lines.append("")  # blank line

        for peer in self.peers:
            peer.validate()
            lines.append("[Peer]")
            lines.extend(peer.to_lines())
            lines.append("")  # blank line

        return "\n".join(lines)

    def save(self) -> None:
        config_text = self.render_config()
        self.path.write_text(config_text)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def load(self, strict: bool = True) -> None:
        """Loads and validates config from disk."""
        if not self.exists():
            raise FileNotFoundError(f"Config file {self.path} does not exist.")

        section_map = parse_sections(self.path, strict=strict)

        if "[Interface]" not in section_map:
            raise ValueError(f"Missing [Interface] section in {self.path}")

        # Parse [Interface] first
        self.interface = WireGuardInterfaceEntry.from_lines(section_map["[Interface]"])
        self.interface.validate()

        # Now remove [Interface] before looping
        del section_map["[Interface]"]

        # Parse remaining sections
        self.peers.clear()
        for section, lines in section_map.items():
            if section == "[Peer]":
                peer = WireGuardPeerEntry.from_lines(lines)
                peer.validate()
                self.peers.append(peer)
            else:
                raise ValueError(f"Unknown section {section} in {self.path}")


class WireGuardInterface:
    def __init__(self, name: str, config_dir: Path = Path("/etc/wireguard")):
        self.name = name
        self.config_dir = config_dir
        self._config = WireGuardInterfaceConfigFile(
            self.config_dir / f"{self.name}.conf"
        )
        self._private_key = WireGuardPrivateKeyFile(
            self.config_dir / "keys" / f"{self.name}.privatekey"
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / f"{self.name}.conf"

    @property
    def private_key_file(self) -> Path:
        return self._private_key.path

    def interface_path(self) -> str:
        """Returns config file path as string, for use by wg-quick."""
        return str(self.config_file)

    def config_exists(self) -> bool:
        return self._config.exists()

    def key_exists(self) -> bool:
        return self._private_key.exists()

    def ensure_directories(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "keys").mkdir(parents=True, exist_ok=True)

    def generate_key(self, overwrite: bool = False) -> None:
        """Generates a private key (public key always derived on the fly)."""
        self.ensure_directories()
        self._private_key.generate(overwrite=overwrite)

    def get_private_key(self) -> str:
        return self._private_key.read()

    def get_public_key(self) -> str:
        return self._private_key.public_key()

    def get_keys(self) -> tuple[str, str]:
        return (self.get_private_key(), self.get_public_key())

    def validate_publickey(self, testkey: str) -> bool:
        return self._private_key.validate_public_key(testkey)

    def ensure_key_exists(self) -> None:
        """Ensures private key exists, otherwise generates."""
        self.ensure_directories()
        if not self._private_key.exists():
            self._private_key.generate()

    def set_interface(self, address: str, listen_port: int, **extra_options) -> None:
        """Sets and saves the [Interface] block."""
        self.ensure_key_exists()
        private_key = self.get_private_key()
        self._config.set_interface(private_key, address, listen_port, **extra_options)

    def add_peer(self, peer: WireGuardPeerEntry) -> None:
        self._config.add_peer(peer)

    def remove_peer(self, peer: WireGuardPeerEntry) -> None:
        self._config.remove_peer(peer)

    def save_config(self) -> None:
        self._config.save()

    def load_config(self) -> None:
        self._config.load()

    def render_config(self) -> str:
        return self._config.render_config()

    @property
    def interface_def(self) -> "WireGuardInterfaceEntry":
        return self._config.interface

    @property
    def peer_defs(self) -> list["WireGuardPeerEntry"]:
        return self._config.peers


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
        if iface.private_key_file.exists():
            iface.private_key_file.unlink()

    def create_interface(
        self,
        name: str,
        address: str,
        listen_port: int,
        save_config: bool = True,
        overwrite: bool = False,
    ) -> WireGuardInterface:
        iface = self.get_interface(name)
        iface.ensure_directories()

        if iface.private_key_file.exists() and not overwrite:
            raise FileExistsError(
                f"Private key already exists for {name}; use overwrite=True to replace."
            )
        if iface.config_file.exists() and not overwrite:
            raise FileExistsError(
                f"Config file already exists for {name}; use overwrite=True to replace."
            )

        iface.generate_key(overwrite=overwrite)
        private_key = iface.get_private_key()

        if save_config:
            config = WireGuardInterfaceConfigFile(iface.config_file)
            config.set_interface(private_key, address, listen_port)
            config.save()

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
