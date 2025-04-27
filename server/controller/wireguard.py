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
    """
    Thin wrapper around the `wg` command-line utility for WireGuard management.

    Provides methods for generating keys, viewing status, and applying configuration
    without manually invoking subprocess calls.

    Args:
        wg_binary: Name or path of the `wg` binary. Defaults to 'wg'.
    """

    def __init__(self, wg_binary: str = "wg"):
        self.wg_binary = wg_binary
        if not shutil.which(self.wg_binary):
            raise FileNotFoundError(
                f"WireGuard binary '{self.wg_binary}' not found in PATH"
            )

    def _run(self, *args: str, input_text: str = None) -> str:
        """
        Internal helper to run a `wg` command and capture its output.

        Args:
            *args: Positional arguments to pass to the `wg` command.
            input_text: Optional text to provide via stdin.

        Returns:
            Standard output from the command.

        Raises:
            WireGuardPermissionError: If permission is denied running the command.
            subprocess.CalledProcessError: For other subprocess errors.
        """
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
        """
        Generates a new WireGuard private key.

        Returns:
            The generated private key as a string.
        """
        return self._run("genkey")

    def genpsk(self) -> str:
        """
        Generates a new WireGuard pre-shared key.

        Returns:
            The generated pre-shared key as a string.
        """
        return self._run("genpsk")

    def pubkey(self, private_key: str) -> str:
        """
        Computes the public key corresponding to a given private key.

        Args:
            private_key: The private key as a string.

        Returns:
            The derived public key as a string.
        """
        return self._run("pubkey", input_text=private_key)

    def show(self, interface: str = None) -> str:
        """
        Shows current WireGuard status.

        Args:
            interface: Optional interface name. If omitted, shows all interfaces.

        Returns:
            Text output from `wg show`.
        """
        if interface:
            return self._run("show", interface)
        return self._run("show")

    def showconf(self, interface: str) -> str:
        """
        Shows the current WireGuard configuration for an interface.

        Args:
            interface: Name of the interface.

        Returns:
            Text output from `wg showconf <interface>`.
        """
        return self._run("showconf", interface)

    def set(self, interface: str, *args: str) -> None:
        """
        Applies runtime configuration changes to an interface.

        Args:
            interface: Name of the interface.
            *args: Additional key-value arguments for the `set` command.

        Example:
            set('wg0', 'peer', '<public-key>', 'allowed-ips', '10.0.0.2/32')
        """
        self._run("set", interface, *args)

    def setconf(self, interface: str, config_file: Path) -> None:
        """
        Replaces the current configuration of an interface with a new configuration file.

        Args:
            interface: Name of the interface.
            config_file: Path to a WireGuard configuration file.
        """
        self._run("setconf", interface, str(config_file))

    def addconf(self, interface: str, config_file: Path) -> None:
        """
        Appends the configuration file to the existing running configuration.

        Args:
            interface: Name of the interface.
            config_file: Path to a WireGuard configuration file.
        """
        self._run("addconf", interface, str(config_file))

    def syncconf(self, interface: str, config_file: Path) -> None:
        """
        Synchronizes the running configuration to match exactly the provided configuration file.

        Args:
            interface: Name of the interface.
            config_file: Path to a WireGuard configuration file.
        """
        self._run("syncconf", interface, str(config_file))


class WireGuardEntry:
    """
    Base class representing a [Section] entry in a WireGuard configuration file.

    This class provides parsing, rendering, and equality comparison for
    configuration sections like [Interface] and [Peer].

    Attributes:
        section_name: The section name string (e.g., "Interface" or "Peer").
                      Must be overridden by subclasses.
        fields: A dictionary mapping field names to their values.
    """

    section_name: str  # To be overridden by subclasses

    def __init__(self, **fields):
        """
        Initialize a WireGuardEntry with arbitrary key-value fields.

        Args:
            fields: Field names and values as keyword arguments.
        """
        self.fields = fields

    @classmethod
    def from_lines(cls, lines: list[str]) -> "WireGuardEntry":
        """
        Parses lines of text into a WireGuardEntry object.

        Args:
            lines: List of "key = value" lines from a configuration file.

        Returns:
            A new instance of the WireGuardEntry subclass.

        Raises:
            ValueError: If a line does not contain an '=' character.
        """
        fields = {}
        for line in lines:
            if "=" in line:
                key, value = map(str.strip, line.split("=", 1))
                fields[key] = value
        return cls(**fields)

    def to_lines(self) -> list[str]:
        """
        Converts the WireGuardEntry to a list of configuration lines.

        Returns:
            List of strings suitable for writing back to a config file,
            including the section header.
        """
        lines = [f"[{self.section_name}]"]
        for key in sorted(self.fields):
            lines.append(f"{key} = {self.fields[key]}")
        return lines

    def __eq__(self, other: object) -> bool:
        """
        Equality comparison based on section name and fields.

        Args:
            other: Another WireGuardEntry instance.

        Returns:
            True if the section names and fields match, otherwise False.
        """
        if not isinstance(other, WireGuardEntry):
            return NotImplemented
        return self.section_name == other.section_name and self.fields == other.fields

    def __repr__(self) -> str:
        """
        Debug string representation.

        Returns:
            Class name and fields dictionary.
        """
        return f"{self.__class__.__name__}({self.fields})"


class WireGuardInterfaceEntry(WireGuardEntry):
    """
    Represents a [Interface] block in a WireGuard configuration file.

    Inherits from:
        WireGuardEntry: Provides basic parsing, rendering, and comparison.

    Attributes:
        section_name (str): Always set to "Interface".
        fields (dict): Key-value pairs representing interface options.
    """

    section_name = "Interface"

    @property
    def private_key(self) -> str:
        """
        Returns:
            The PrivateKey field, or None if not set.
        """
        return self.fields.get("PrivateKey")

    @property
    def address(self) -> str:
        """
        Returns:
            The Address field, or None if not set.
        """
        return self.fields.get("Address")

    @property
    def listen_port(self) -> str:
        """
        Returns:
            The ListenPort field, or None if not set.
        """
        return self.fields.get("ListenPort")

    def validate(self) -> None:
        """
        Validates that required fields are present and all keys are allowed.

        Raises:
            ValueError: If required fields are missing or unknown fields are present.
        """
        if "PrivateKey" not in self.fields:
            raise ValueError(
                "Missing required field 'PrivateKey' in [Interface] section."
            )

        for key in self.fields:
            if key not in INTERFACE_ALLOWED_FIELDS:
                raise ValueError(f"Unknown field '{key}' in [Interface] section.")

    def __repr__(self) -> str:
        """
        Returns:
            A concise string representation, redacting the PrivateKey value.
        """
        important_fields = []
        for key in ["PrivateKey", "Address", "ListenPort"]:
            if key in self.fields:
                val = "redacted" if key == "PrivateKey" else self.fields[key]
                important_fields.append(f"{key}={val}")
        return f"WireGuardInterfaceEntry({', '.join(important_fields)})"


class WireGuardPeerEntry(WireGuardEntry):
    """
    Represents a [Peer] block in a WireGuard configuration file.

    Inherits from:
        WireGuardEntry: Provides basic parsing, rendering, and comparison.

    Attributes:
        section_name (str): Always set to "Peer".
        fields (dict): Key-value pairs representing peer options.
    """

    section_name = "Peer"

    @property
    def public_key(self) -> str:
        """
        Returns:
            The PublicKey field, or None if not set.
        """
        return self.fields.get("PublicKey")

    @property
    def allowed_ips(self) -> str:
        """
        Returns:
            The AllowedIPs field, or None if not set.
        """
        return self.fields.get("AllowedIPs")

    @property
    def endpoint(self) -> str:
        """
        Returns:
            The Endpoint field, or None if not set.
        """
        return self.fields.get("Endpoint")

    @property
    def persistent_keepalive(self) -> str:
        """
        Returns:
            The PersistentKeepalive field, or None if not set.
        """
        return self.fields.get("PersistentKeepalive")

    def validate(self) -> None:
        """
        Validates that required fields are present and all keys are allowed.

        Raises:
            ValueError: If required fields are missing or unknown fields are present.
        """
        for required in ("PublicKey", "AllowedIPs"):
            if required not in self.fields:
                raise ValueError(
                    f"Missing required field '{required}' in [Peer] section."
                )

        for key in self.fields:
            if key not in PEER_ALLOWED_FIELDS:
                raise ValueError(f"Unknown field '{key}' in [Peer] section.")

    def __repr__(self) -> str:
        """
        Returns:
            A concise string representation highlighting key fields.
        """
        important_fields = []
        for key in ["PublicKey", "AllowedIPs", "Endpoint"]:
            if key in self.fields:
                important_fields.append(f"{key}={self.fields[key]}")
        return f"WireGuardPeerEntry({', '.join(important_fields)})"


class WireGuardInterfaceConfigFile:
    """
    Manages reading and writing WireGuard configuration files at the file level.

    Responsibilities:
        - Load and validate [Interface] and [Peer] sections from disk.
        - Save a given [Interface] and list of [Peer] entries to disk.
        - Ensure the containing directory exists before saving.
    """

    def __init__(self, path: Path):
        """
        Args:
            path: Path to the WireGuard configuration file (.conf).
        """
        self.path = path

    @property
    def config_dir(self) -> Path:
        """
        Returns:
            Directory containing the config file.
        """
        return self.path.parent

    def ensure_directories(self) -> None:
        """
        Ensures the parent directory exists, creating it if necessary.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        """
        Returns:
            True if the config file exists on disk.
        """
        return self.path.exists()

    def load(
        self, strict: bool = True
    ) -> tuple[WireGuardInterfaceEntry, list[WireGuardPeerEntry]]:
        """
        Loads and validates configuration data from disk.

        Args:
            strict: If True, fail if lines are outside sections. If False, ignore them.

        Returns:
            A tuple (interface_entry, list_of_peer_entries).

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If required sections are missing, sections are duplicated improperly,
                        or unknown sections are present.
        """
        if not self.exists():
            raise FileNotFoundError(f"Config file {self.path} does not exist.")

        section_map = parse_sections(self.path, strict=strict)

        if "[Interface]" not in section_map:
            raise ValueError(f"Missing [Interface] section in {self.path}")

        # Parse [Interface] sections
        interface_entries = []
        for section, lines in section_map.items():
            if section == "[Interface]":
                entry = WireGuardInterfaceEntry.from_lines(lines)
                entry.validate()
                interface_entries.append(entry)

        # Validate that all [Interface] sections are identical
        first_entry = interface_entries[0]
        for other_entry in interface_entries[1:]:
            if first_entry != other_entry:
                raise ValueError(
                    f"Multiple [Interface] sections with different contents in {self.path}"
                )

        interface_entry = first_entry

        # Remove [Interface] sections from map
        section_map = {k: v for k, v in section_map.items() if k != "[Interface]"}

        # Parse [Peer] sections
        peer_entries = []
        for section, lines in section_map.items():
            if section == "[Peer]":
                peer = WireGuardPeerEntry.from_lines(lines)
                peer.validate()
                peer_entries.append(peer)
            else:
                raise ValueError(f"Unknown section {section} in {self.path}")

        return interface_entry, peer_entries

    def save(
        self,
        interface_entry: WireGuardInterfaceEntry,
        peer_entries: list[WireGuardPeerEntry],
        overwrite: bool = False,
    ) -> None:
        """
        Saves a [Interface] entry and [Peer] entries to disk.

        Args:
            interface_entry: The interface entry to save.
            peer_entries: List of peer entries to save.
            overwrite: If False, raise an error if file already exists.

        Raises:
            FileExistsError: If the file already exists and overwrite is False.
        """
        if self.exists() and not overwrite:
            raise FileExistsError(f"Config file already exists at {self.path}")

        lines = []

        # Write [Interface] block
        lines.append("[Interface]")
        for key in sorted(interface_entry.fields):
            lines.append(f"{key} = {interface_entry.fields[key].strip()}")
        lines.append("")  # blank line after [Interface]

        # Write [Peer] blocks
        for peer in peer_entries:
            peer.validate()
            lines.append("[Peer]")
            for key in sorted(peer.fields):
                lines.append(f"{key} = {peer.fields[key].strip()}")
            lines.append("")  # blank line after each [Peer]

        if lines and lines[-1] == "":
            lines.pop()

        config_text = "\n".join(lines)
        self.path.write_text(config_text)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)


class WireGuardInterface:
    """
    Represents a WireGuard interface configuration.

    Provides in-memory management of [Interface] and [Peer] entries,
    handles loading/saving configs, and manages private key operations.

    Attributes:
        name: Interface name (without ".conf").
        _config: WireGuardInterfaceConfigFile object tied to the interface's config file.
        interface_entry: WireGuardInterfaceEntry holding the [Interface] block.
        peer_entries: List of WireGuardPeerEntry objects for [Peer] blocks.
        _wg: WireGuard helper for key generation and public key computation.
    """

    def __init__(self, name: str, config_dir: Path = Path("/etc/wireguard")):
        """
        Initializes the WireGuard interface manager.

        Args:
            name: Interface name (e.g., 'wg0').
            config_dir: Base directory where configs are stored.
        """
        self.name = name
        self._config = WireGuardInterfaceConfigFile(config_dir / f"{name}.conf")
        self.interface_entry: WireGuardInterfaceEntry = None
        self.peer_entries: list[WireGuardPeerEntry] = []
        self._wg = WireGuard()

    @property
    def config_file(self) -> Path:
        """Returns the full path to the .conf file."""
        return self._config.path

    def interface_path(self) -> str:
        """Returns the config file path as a string (for wg-quick and subprocesses)."""
        return str(self.config_file)

    def config_exists(self) -> bool:
        """Returns True if the config file exists on disk."""
        return self._config.exists()

    def ensure_directories(self) -> None:
        """Ensures the parent directory for the config file exists."""
        self._config.ensure_directories()

    def load_config(self) -> None:
        """
        Loads the config file into memory.

        Raises:
            FileNotFoundError: If the config file is missing.
            ValueError: If the config file is invalid.
        """
        self.interface_entry, self.peer_entries = self._config.load()

    def save_config(self, overwrite: bool = False) -> None:
        """
        Saves the current in-memory interface and peers to disk.

        Args:
            overwrite: If True, overwrites an existing config file.

        Raises:
            FileExistsError: If config exists and overwrite=False.
            ValueError: If no interface entry is set.
        """
        if self.interface_entry is None:
            raise ValueError("No interface set.")
        self._config.save(self.interface_entry, self.peer_entries, overwrite=overwrite)

    def set_interface(
        self,
        address: str,
        listen_port: int,
        private_key: str = None,
        **extra_options,
    ) -> None:
        """
        Creates or sets the [Interface] block.

        Args:
            address: IP address and subnet (e.g., "10.0.0.1/24").
            listen_port: UDP port number to listen on.
            private_key: Optional private key. If not provided, a key is generated.
            extra_options: Additional key-value pairs for [Interface].
        """
        self.ensure_directories()

        if private_key is None:
            private_key = self._wg.genkey()

        self.interface_entry = WireGuardInterfaceEntry(
            PrivateKey=private_key,
            Address=address,
            ListenPort=str(listen_port),
            **extra_options,
        )

    def add_private_key_if_missing(self) -> None:
        """
        Ensures that a PrivateKey exists in the interface entry.

        Only generates a key if missing. Does NOT overwrite.

        Raises:
            ValueError: If no interface entry is loaded.
        """
        if self.interface_entry is None:
            raise ValueError(f"Interface not loaded or set for {self.name}.")
        if "PrivateKey" not in self.interface_entry.fields:
            private_key = self._wg.genkey()
            self.interface_entry.fields["PrivateKey"] = private_key

    def generate_private_key(self, overwrite: bool = False) -> None:
        """
        Generates a new PrivateKey.

        Args:
            overwrite: If False, raises an error if key already exists.

        Raises:
            ValueError: If key exists and overwrite=False, or no interface loaded.
        """
        if self.interface_entry is None:
            raise ValueError(f"Interface not loaded or set for {self.name}.")
        if "PrivateKey" in self.interface_entry.fields and not overwrite:
            raise ValueError(
                f"PrivateKey already exists for {self.name}. Pass overwrite=True to replace."
            )
        private_key = self._wg.genkey()
        self.interface_entry.fields["PrivateKey"] = private_key

    def get_private_key(self) -> str:
        """Returns the PrivateKey value."""
        if self.interface_entry is None:
            raise ValueError(f"Interface not loaded or set for {self.name}.")
        return self.interface_entry.fields["PrivateKey"]

    def get_public_key(self) -> str:
        """Computes and returns the corresponding PublicKey."""
        return self._wg.pubkey(self.get_private_key())

    def get_keys(self) -> tuple[str, str]:
        """Returns (private_key, public_key) tuple."""
        private_key = self.get_private_key()
        public_key = self.get_public_key()
        return (private_key, public_key)

    def validate_publickey(self, testkey: str) -> bool:
        """
        Validates that the given public key matches the private key.

        Args:
            testkey: Public key to test.

        Returns:
            True if matching, False otherwise.
        """
        return self.get_public_key() == testkey

    def add_peer(self, peer: WireGuardPeerEntry) -> None:
        """Adds a peer entry."""
        self.peer_entries.append(peer)

    def remove_peer(self, peer: WireGuardPeerEntry) -> None:
        """Removes a peer entry."""
        self.peer_entries.remove(peer)

    def render_config(self) -> str:
        """Renders the current in-memory config as a text string."""
        return self._config.render_config(self.interface_entry, self.peer_entries)

    @property
    def interface_def(self) -> WireGuardInterfaceEntry:
        """Returns the current [Interface] entry."""
        return self.interface_entry

    @property
    def peer_defs(self) -> list[WireGuardPeerEntry]:
        """Returns the list of [Peer] entries."""
        return self.peer_entries


class WireGuardConfigurationError(Exception):
    """Base exception for WireGuard configuration errors."""


class InterfaceNotFoundError(WireGuardConfigurationError):
    """Raised when the requested interface config file does not exist."""


class InterfaceAlreadyExistsError(WireGuardConfigurationError):
    """Raised when trying to create an interface that already exists."""


class WireGuardConfiguration:
    """Manages WireGuard interface configurations in a given directory."""

    def __init__(self, config_dir: Path = Path("/etc/wireguard")):
        """
        Args:
            config_dir: Directory where .conf files are stored.
        """
        self.config_dir = config_dir

    def interfaces(self) -> list[WireGuardInterface]:
        """
        Returns:
            List of WireGuardInterface objects for all existing .conf files.
        """
        return [
            WireGuardInterface(p.stem, self.config_dir)
            for p in self.config_dir.glob("*.conf")
        ]

    def get_interface(self, name: str) -> WireGuardInterface:
        """
        Args:
            name: Name of the interface (without .conf extension).

        Returns:
            WireGuardInterface object.

        Raises:
            InterfaceNotFoundError: If the interface config file does not exist.
        """
        iface = WireGuardInterface(name, self.config_dir)
        if not iface.config_exists():
            raise InterfaceNotFoundError(f"Interface '{name}' does not exist.")
        return iface

    def remove_interface(self, name: str) -> WireGuardInterface:
        """
        Deletes the .conf file for the given interface.

        Args:
            name: Name of the interface (without .conf extension).

        Returns:
            WireGuardInterface object.

        Raises:
            InterfaceNotFoundError: If the config file does not exist.
        """
        iface = self.get_interface(name)
        iface.config_file.unlink()
        return iface

    def create_interface(
        self,
        name: str,
        address: str,
        listen_port: int,
        private_key: str = None,
        save: bool = True,
        **extra_options,
    ) -> WireGuardInterface:
        """
        Creates a new WireGuard interface configuration.

        Args:
            name: Interface name (without .conf extension).
            address: IP address and subnet (e.g., "10.0.0.1/24").
            listen_port: UDP port to listen on.
            private_key: Optional private key. If not provided, a key is generated.
            save: If True (default), writes the config file immediately.
            extra_options: Additional optional settings for the [Interface] block.

        Returns:
            WireGuardInterface object.

        Raises:
            InterfaceAlreadyExistsError: If a config file already exists.
        """
        iface = WireGuardInterface(name, self.config_dir)
        if iface.config_exists():
            raise InterfaceAlreadyExistsError(f"Interface '{name}' already exists.")
        iface.set_interface(address, listen_port, private_key, **extra_options)
        if save:
            iface.save_config()
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
