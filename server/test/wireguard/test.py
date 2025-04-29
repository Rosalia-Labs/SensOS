# test_wireguard.py

import os
import shutil
import tempfile
import pytest
from pathlib import Path
from wireguard import (
    parse_sections,
    WireGuardInterfaceConfigFile,
    WireGuardInterfaceEntry,
    WireGuardPeerEntry,
    WireGuardInterface,
    WireGuardConfiguration,
    WireGuard,
)


@pytest.fixture
def tempdir():
    path = Path(tempfile.mkdtemp())
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_parse_sections_simple(tempdir):
    f = tempdir / "config.conf"
    f.write_text(
        """
    [Interface]
    PrivateKey = abcdef
    Address = 10.0.0.1/24

    [Peer]
    PublicKey = xyz
    AllowedIPs = 0.0.0.0/0
    """
    )

    sections = parse_sections(f)

    assert "Interface" in sections
    assert "Peer" in sections
    assert "PrivateKey = abcdef" in sections["Interface"][0]
    assert "PublicKey = xyz" in sections["Peer"][0]


def test_config_file_save_and_load(tempdir):
    config_file = tempdir / "wg0.conf"
    config = WireGuardInterfaceConfigFile(config_file)

    # Create in-memory entries manually
    interface_entry = WireGuardInterfaceEntry(
        PrivateKey="privkey", Address="10.0.0.1/24", ListenPort="51820"
    )
    peer_entry = WireGuardPeerEntry(PublicKey="peerkey", AllowedIPs="0.0.0.0/0")

    # Save to file
    config.save(interface_entry, [peer_entry])

    # Now load back
    loaded_interface, loaded_peers = config.load()

    assert loaded_interface.private_key == "privkey"
    assert loaded_interface.address == "10.0.0.1/24"
    assert loaded_interface.listen_port == "51820"
    assert len(loaded_peers) == 1
    assert loaded_peers[0].public_key == "peerkey"


def test_invalid_peer_missing_fields(tempdir):
    config_file = tempdir / "bad.conf"
    config_file.write_text(
        """
    [Interface]
    PrivateKey = dummy
    Address = 10.0.0.1/24
    ListenPort = 51820

    [Peer]
    AllowedIPs = 0.0.0.0/0
    """
    )

    config = WireGuardInterfaceConfigFile(config_file)

    with pytest.raises(ValueError, match="Missing required field 'PublicKey'"):
        config.load()


def test_invalid_interface_missing_privatekey(tempdir):
    config_file = tempdir / "bad2.conf"
    config_file.write_text(
        """
    [Interface]
    Address = 10.0.0.1/24
    ListenPort = 51820
    """
    )

    config = WireGuardInterfaceConfigFile(config_file)

    with pytest.raises(ValueError, match="Missing required field 'PrivateKey'"):
        config.load()


def test_interface_end_to_end(tempdir):
    iface = WireGuardInterface(name="wg-test", config_dir=tempdir)
    iface.set_interface(
        address="10.0.0.1/24",
        listen_port=51820,
        private_key="dummy_private_key",
    )
    peer = WireGuardPeerEntry(PublicKey="somepubkey", AllowedIPs="10.0.0.2/32")
    iface.add_peer(peer)
    iface.save_config()

    assert iface.config_file.exists()

    iface2 = WireGuardInterface(name="wg-test", config_dir=tempdir)
    iface2.load_config()

    assert iface2.interface_def.address == "10.0.0.1/24"
    assert iface2.peer_defs[0].allowed_ips == "10.0.0.2/32"


def test_wireguard_configuration(tempdir):
    config = WireGuardConfiguration(config_dir=tempdir)
    key = WireGuard().genkey()

    iface = config.create_interface(
        name="wg0",
        address="10.0.0.1/24",
        listen_port=51820,
        private_key=key,  # ✅ Explicit key
    )

    assert iface.config_file.exists()

    all_ifaces = config.interfaces()
    assert len(all_ifaces) == 1
    assert all_ifaces[0].name == "wg0"

    config.remove_interface("wg0")
    assert not iface.config_file.exists()


def test_minimal_valid_config(tempdir):
    config_file = tempdir / "good.conf"
    config_file.write_text(
        """
        [Interface]
        PrivateKey = ABCDEF
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = PEERKEY
        AllowedIPs = 0.0.0.0/0
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()

    assert interface_entry.private_key == "ABCDEF"
    assert interface_entry.address == "10.0.0.1/24"
    assert interface_entry.listen_port == "51820"
    assert len(peer_entries) == 1
    assert peer_entries[0].public_key == "PEERKEY"


def test_blank_file(tempdir):
    config_file = tempdir / "blank.conf"
    config_file.write_text("")

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing \\[Interface\\] section"):
        config.load()


def test_missing_interface_section(tempdir):
    config_file = tempdir / "no_interface.conf"
    config_file.write_text(
        """
    [Peer]
    PublicKey = PEERKEY
    AllowedIPs = 0.0.0.0/0
    """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing \\[Interface\\] section"):
        config.load()


def test_unknown_section(tempdir):
    config_file = tempdir / "unknown_section.conf"
    config_file.write_text(
        """
    [Interface]
    PrivateKey = ABCDEF
    Address = 10.0.0.1/24
    ListenPort = 51820

    [UnknownSection]
    Foo = Bar
    """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Unknown section \\[UnknownSection\\]"):
        config.load()


def test_duplicate_interface_sections(tempdir):
    config_file = tempdir / "duplicate_interface.conf"
    config_file.write_text(
        """
        [Interface]
        PrivateKey = ABCDEF
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Interface]
        PrivateKey = XYZ
        Address = 10.0.0.1/24
        ListenPort = 51820
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(
        ValueError, match="Multiple \\[Interface\\] sections with different contents"
    ):
        config.load()


def test_unknown_field_in_peer(tempdir):
    config_file = tempdir / "bad_field.conf"
    config_file.write_text(
        """
    [Interface]
    PrivateKey = ABCDEF
    Address = 10.0.0.1/24
    ListenPort = 51820

    [Peer]
    PublicKey = PEERKEY
    AllowedIPs = 0.0.0.0/0
    Foo = Bar
    """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Unknown field 'Foo' in \\[Peer\\] section"):
        config.load()


def test_peer_missing_allowed_ips(tempdir):
    config_file = tempdir / "missing_allowedips.conf"
    config_file.write_text(
        """
    [Interface]
    PrivateKey = ABCDEF
    Address = 10.0.0.1/24
    ListenPort = 51820

    [Peer]
    PublicKey = PEERKEY
    """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing required field 'AllowedIPs'"):
        config.load()


def test_line_outside_section_strict(tempdir):
    config_file = tempdir / "outside_line.conf"
    config_file.write_text(
        """
    This is a bad line

    [Interface]
    PrivateKey = ABCDEF
    Address = 10.0.0.1/24
    ListenPort = 51820
    """
    )

    with pytest.raises(ValueError, match="Line outside any section"):
        parse_sections(config_file, strict=True)


def test_line_outside_section_non_strict(tempdir):
    config_file = tempdir / "outside_line_nonstrict.conf"
    config_file.write_text(
        """
    This is a bad line

    [Interface]
    PrivateKey = ABCDEF
    Address = 10.0.0.1/24
    ListenPort = 51820
    """
    )

    section_map = parse_sections(config_file, strict=False)
    assert "Interface" in section_map
    assert any("PrivateKey = ABCDEF" in l for l in section_map["Interface"])


def test_wireguard_interface_entry_from_lines_and_to_lines():
    lines = [
        "PrivateKey = abc123",
        "Address = 10.0.0.1/24",
        "ListenPort = 51820",
    ]
    entry = WireGuardInterfaceEntry.from_lines(lines)
    assert entry.private_key == "abc123"
    assert entry.address == "10.0.0.1/24"
    assert entry.listen_port == "51820"

    output_lines = entry.to_lines()
    assert output_lines[0] == "[Interface]"
    assert "PrivateKey = abc123" in output_lines
    assert "Address = 10.0.0.1/24" in output_lines
    assert "ListenPort = 51820" in output_lines


def test_wireguard_peer_entry_from_lines_and_to_lines():
    lines = [
        "PublicKey = def456",
        "AllowedIPs = 0.0.0.0/0",
        "Endpoint = example.com:51820",
    ]
    peer = WireGuardPeerEntry.from_lines(lines)
    assert peer.public_key == "def456"
    assert peer.allowed_ips == "0.0.0.0/0"
    assert peer.endpoint == "example.com:51820"

    output_lines = peer.to_lines()
    assert output_lines[0] == "[Peer]"
    assert "PublicKey = def456" in output_lines
    assert "AllowedIPs = 0.0.0.0/0" in output_lines
    assert "Endpoint = example.com:51820" in output_lines


def test_interface_entry_validation_success():
    entry = WireGuardInterfaceEntry(
        PrivateKey="abc123",
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    entry.validate()  # Should not raise


def test_peer_entry_validation_success():
    peer = WireGuardPeerEntry(
        PublicKey="def456",
        AllowedIPs="0.0.0.0/0",
    )
    peer.validate()  # Should not raise


def test_interface_entry_missing_private_key():
    entry = WireGuardInterfaceEntry(
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    with pytest.raises(ValueError, match="Missing required field 'PrivateKey'"):
        entry.validate()


def test_peer_entry_missing_public_key():
    peer = WireGuardPeerEntry(
        AllowedIPs="0.0.0.0/0",
    )
    with pytest.raises(ValueError, match="Missing required field 'PublicKey'"):
        peer.validate()


def test_peer_entry_unknown_field():
    peer = WireGuardPeerEntry(
        PublicKey="def456",
        AllowedIPs="0.0.0.0/0",
        UnknownField="oops",
    )
    with pytest.raises(
        ValueError, match="Unknown field 'UnknownField' in \\[Peer\\] section"
    ):
        peer.validate()


def test_entries_equality():
    entry1 = WireGuardInterfaceEntry(
        PrivateKey="abc123",
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    entry2 = WireGuardInterfaceEntry(
        PrivateKey="abc123",
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    assert entry1 == entry2

    peer1 = WireGuardPeerEntry(
        PublicKey="def456",
        AllowedIPs="0.0.0.0/0",
    )
    peer2 = WireGuardPeerEntry(
        PublicKey="def456",
        AllowedIPs="0.0.0.0/0",
    )
    assert peer1 == peer2


def test_entries_inequality_different_section():
    entry = WireGuardInterfaceEntry(
        PrivateKey="abc123",
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    peer = WireGuardPeerEntry(
        PublicKey="abc123",
        AllowedIPs="10.0.0.0/24",
    )
    assert entry != peer


def test_whitespace_in_section_name(tempdir):
    """Test that a section name with spaces is treated as missing [Interface]."""
    config_file = tempdir / "bad_section.conf"
    config_file.write_text(
        """
        [The Interface]
        PrivateKey = ABCDEF
        Address = 10.0.0.1/24
        ListenPort = 51820
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing \\[Interface\\] section"):
        config.load()


def test_whitespace_in_key_or_value(tempdir):
    """Test that whitespace in key or value does not crash, but is preserved."""
    config_file = tempdir / "whitespace_key_value.conf"
    config_file.write_text(
        """
        [Interface]
        PrivateKey    =    ABCDEF
        Address    =    10.0.0.1/24
        ListenPort   =   51820

        [Peer]
        PublicKey   =   PEERKEY
        AllowedIPs  =   0.0.0.0/0
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()

    assert interface_entry.private_key == "ABCDEF"
    assert interface_entry.address == "10.0.0.1/24"
    assert interface_entry.listen_port == "51820"
    assert peer_entries[0].public_key == "PEERKEY"
    assert peer_entries[0].allowed_ips == "0.0.0.0/0"


def test_malformed_ip_in_interface(tempdir):
    """Test that a malformed Address does not raise (currently no IP format checking)."""
    config_file = tempdir / "bad_ip.conf"
    config_file.write_text(
        """
        [Interface]
        PrivateKey = ABCDEF
        Address = not_an_ip
        ListenPort = 51820
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()
    assert interface_entry.address == "not_an_ip"


def test_malformed_ip_in_peer(tempdir):
    """Test that a malformed AllowedIPs does not raise (currently no IP format checking)."""
    config_file = tempdir / "bad_peer_ip.conf"
    config_file.write_text(
        """
        [Interface]
        PrivateKey = ABCDEF
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = PEERKEY
        AllowedIPs = 999.999.999.999/99
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()
    assert peer_entries[0].allowed_ips == "999.999.999.999/99"


def test_malformed_endpoint_in_peer(tempdir):
    """Test that a malformed Endpoint does not raise (currently no format checking)."""
    config_file = tempdir / "bad_endpoint.conf"
    config_file.write_text(
        """
        [Interface]
        PrivateKey = ABCDEF
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = PEERKEY
        AllowedIPs = 0.0.0.0/0
        Endpoint = 300.300.300.300:12345
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()
    assert peer_entries[0].endpoint == "300.300.300.300:12345"
