# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import os
import re
import stat
import shutil
import subprocess
from pathlib import Path
from typing import Optional

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

INTERFACE_REQUIRED_FIELDS = {"PrivateKey"}
PEER_REQUIRED_FIELDS = {"PublicKey", "AllowedIPs"}

BASE64_32_BYTE_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _is_valid_wg_key(key: str) -> bool:
    return isinstance(key, str) and BASE64_32_BYTE_RE.fullmatch(key) is not None


def _validate_no_ip_conflicts(self) -> None:
    """
    Ensures that the interface IP and all peer AllowedIPs do not overlap.
    """
    used_ips = set()

    if self.interface_def.address:
        ip = self.interface_def.address.split("/")[0]
        if ip in used_ips:
            raise ValueError(f"Duplicate IP address in interface: {ip}")
        used_ips.add(ip)

    for peer in self.peer_defs:
        allowed = peer.allowed_ips.split(",")
        for cidr in allowed:
            ip = cidr.strip().split("/")[0]
            if ip in used_ips:
                raise ValueError(f"IP conflict between interface and peer: {ip}")
            used_ips.add(ip)


def _parse_sections(path: Path, strict: bool = True) -> dict[str, list[list[str]]]:
    section_map = {}
    current_section = None
    current_lines = []

    with path.open() as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                if current_section is not None:
                    section_map.setdefault(current_section, []).append(current_lines)
                current_section = line[1:-1].strip()
                current_lines = []
            else:
                if current_section is None:
                    if strict:
                        raise ValueError("Line outside any section")
                    else:
                        continue
                current_lines.append(line)

    if current_section is not None:
        section_map.setdefault(current_section, []).append(current_lines)

    return section_map


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
        missing = INTERFACE_REQUIRED_FIELDS - self.fields.keys()
        if missing:
            raise ValueError(
                f"Missing required field(s) in [Interface] section: {', '.join(missing)}"
            )
        for key in self.fields:
            if key not in INTERFACE_ALLOWED_FIELDS:
                raise ValueError(f"Unknown field '{key}' in [Interface] section.")
        if "PrivateKey" in self.fields and not _is_valid_wg_key(
            self.fields["PrivateKey"]
        ):
            raise ValueError("Invalid PrivateKey format in [Interface] section.")

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
        missing = PEER_REQUIRED_FIELDS - self.fields.keys()
        if missing:
            raise ValueError(
                f"Missing required field(s) in [Peer] section: {', '.join(missing)}"
            )
        for key in self.fields:
            if key not in PEER_ALLOWED_FIELDS:
                raise ValueError(f"Unknown field '{key}' in [Peer] section.")
        if "PublicKey" in self.fields and not _is_valid_wg_key(
            self.fields["PublicKey"]
        ):
            raise ValueError("Invalid PublicKey format in [Peer] section.")
        if "PresharedKey" in self.fields and not _is_valid_wg_key(
            self.fields["PresharedKey"]
        ):
            raise ValueError("Invalid PresharedKey format in [Peer] section.")

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
            ValueError: If required sections are missing, duplicated improperly,
                        or unknown sections are present.
        """
        if not self.exists():
            raise FileNotFoundError(f"Config file {self.path} does not exist.")

        section_map = _parse_sections(self.path, strict=strict)

        if "Interface" not in section_map:
            raise ValueError(f"Missing [Interface] section in {self.path}")

        # Parse [Interface] sections
        interface_entries = []
        for entry_lines in section_map.get("Interface", []):
            entry = WireGuardInterfaceEntry.from_lines(entry_lines)
            entry.validate()
            interface_entries.append(entry)

        # Validate that all [Interface] sections are identical
        if len(interface_entries) > 1:
            first_entry = interface_entries[0]
            for other_entry in interface_entries[1:]:
                if first_entry != other_entry:
                    raise ValueError(
                        f"Multiple [Interface] sections with different contents in {self.path}"
                    )
        interface_entry = interface_entries[0]

        # Parse [Peer] sections
        peer_entries = []
        for entry_lines in section_map.get("Peer", []):
            peer = WireGuardPeerEntry.from_lines(entry_lines)
            peer.validate()
            peer_entries.append(peer)

        # Check for unknown sections
        known_sections = {"Interface", "Peer"}
        for section_name in section_map.keys():
            if strict and section_name not in known_sections:
                raise ValueError(f"Unknown section [{section_name}] in {self.path}")

        return interface_entry, peer_entries

    def save(
        self,
        interface_entry: WireGuardInterfaceEntry,
        peer_entries: list[WireGuardPeerEntry],
        overwrite: bool = False,
    ) -> None:
        if self.exists() and not overwrite:
            raise FileExistsError(f"Config file already exists at {self.path}")

        # Create a temporary WireGuardInterface object to run validation logic
        wg_iface = WireGuardInterface(self.path.stem, self.path.parent)
        wg_iface.set_interface(interface_entry)
        for peer in peer_entries:
            wg_iface.add_peer(peer)

        # Validate IP conflicts before saving
        _validate_no_ip_conflicts(wg_iface)

        lines = []

        interface_entry.validate()
        lines.append("[Interface]")
        for key in sorted(interface_entry.fields):
            value = str(interface_entry.fields[key]).strip()
            lines.append(f"{key} = {value}")
        lines.append("")

        for peer in peer_entries:
            peer.validate()
            lines.append("[Peer]")
            for key in sorted(peer.fields):
                value = str(peer.fields[key]).strip()
                lines.append(f"{key} = {value}")
            lines.append("")

        config_text = "\n".join(lines)
        self.path.write_text(config_text)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)


class WireGuardInterface:
    """
    Represents a WireGuard interface configuration.

    Provides in-memory management of [Interface] and [Peer] entries,
    handles loading/saving configs, and supports setting fields manually.

    Attributes:
        name: Interface name (without ".conf").
        _config: WireGuardInterfaceConfigFile object tied to the interface's config file.
        interface_entry: WireGuardInterfaceEntry holding the [Interface] block.
        peer_entries: List of WireGuardPeerEntry objects for [Peer] blocks.
    """

    def __init__(self, name: str, config_dir: Path = Path("/etc/wireguard")):
        """
        Initializes the WireGuard interface manager.

        Args:
            name: Name of the interface (e.g., 'wg0').
            config_dir: Directory where config files are stored.
        """
        self.name = name
        self._config = WireGuardInterfaceConfigFile(config_dir / f"{name}.conf")
        self.interface_entry: Optional[WireGuardInterfaceEntry] = None
        self.peer_entries: list[WireGuardPeerEntry] = []

    @property
    def config_file(self) -> Path:
        """
        Returns:
            The full path to the interface's configuration file.
        """
        return self._config.path

    def interface_path(self) -> str:
        """
        Returns:
            The config file path as a string (e.g., for wg-quick or subprocesses).
        """
        return str(self.config_file)

    def config_exists(self) -> bool:
        """
        Returns:
            True if the config file exists on disk, False otherwise.
        """
        return self._config.exists()

    def ensure_directories(self) -> None:
        """
        Ensures that the parent directory of the config file exists.
        Creates it if necessary.
        """
        self._config.ensure_directories()

    def load_config(self) -> None:
        """
        Loads the interface and peer configuration from the config file.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If the config file is malformed or invalid.
        """
        self.interface_entry, self.peer_entries = self._config.load()

    def save_config(self, overwrite: bool = False) -> None:
        """
        Saves the current interface and peer entries to the config file.

        Args:
            overwrite: If True, allows overwriting an existing config file.

        Raises:
            FileExistsError: If file exists and overwrite is False.
            ValueError: If no interface entry is defined.
        """
        if self.interface_entry is None:
            raise ValueError("No interface set.")
        self._config.save(self.interface_entry, self.peer_entries, overwrite=overwrite)

    def set_interface(self, entry: WireGuardInterfaceEntry) -> None:
        """
        Sets the WireGuard [Interface] block for this interface.

        Args:
            entry: A valid WireGuardInterfaceEntry instance.

        Raises:
            ValueError: If the entry fails validation.
        """
        entry.validate()
        self.interface_entry = entry

    def get_private_key(self) -> str:
        """
        Returns:
            The current private key set in the [Interface] block.

        Raises:
            ValueError: If no interface has been loaded or initialized.
        """
        if self.interface_entry is None:
            raise ValueError(f"Interface not loaded or set for {self.name}.")
        return self.interface_entry.fields["PrivateKey"]

    def add_peer(self, peer: WireGuardPeerEntry) -> None:
        """
        Appends a new [Peer] entry to the configuration.

        Args:
            peer: The WireGuardPeerEntry to add.
        """
        self.peer_entries.append(peer)

    def remove_peer(self, peer: WireGuardPeerEntry) -> None:
        """
        Removes an existing [Peer] entry from the configuration.

        Args:
            peer: The WireGuardPeerEntry to remove.
        """
        self.peer_entries.remove(peer)

    def render_config(self) -> str:
        """
        Returns:
            A full rendered WireGuard configuration string.
        """
        return self._config.render_config(self.interface_entry, self.peer_entries)

    @property
    def interface_def(self) -> WireGuardInterfaceEntry:
        """
        Returns:
            The current WireGuardInterfaceEntry (i.e., the [Interface] block).
        """
        return self.interface_entry

    @property
    def peer_defs(self) -> list[WireGuardPeerEntry]:
        """
        Returns:
            A list of all [Peer] entries currently configured.
        """
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
        self, name: str, interface_entry: WireGuardInterfaceEntry, save: bool = True
    ) -> WireGuardInterface:
        """
        Creates a new WireGuard interface configuration using a complete [Interface] entry.

        This method constructs a `WireGuardInterface` object from the provided `WireGuardInterfaceEntry`
        and optionally saves it to disk.

        Args:
            name: Interface name (without the '.conf' extension).
            interface_entry: A fully constructed `WireGuardInterfaceEntry` defining the [Interface] block.
            save: If True (default), immediately writes the configuration to a file.

        Returns:
            A `WireGuardInterface` instance with the given configuration.

        Raises:
            InterfaceAlreadyExistsError: If a configuration file for the interface already exists.
        """
        iface = WireGuardInterface(name, self.config_dir)
        iface.set_interface(interface_entry)
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

    def up(self, iface: WireGuardInterface) -> None:
        subprocess.run(
            ["wg-quick", "up", iface.name],
            check=True,
        )

    def down(self, iface: WireGuardInterface) -> None:
        subprocess.run(
            ["wg-quick", "down", iface.name],
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
