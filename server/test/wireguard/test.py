# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

# test_wireguard.py

import os
import shutil
import base64
import pytest
import tempfile
from pathlib import Path
from wireguard import (
    _parse_sections,
    WireGuardInterfaceConfigFile,
    WireGuardInterfaceEntry,
    WireGuardPeerEntry,
    WireGuardInterface,
    WireGuardConfiguration,
    WireGuard,
)


def _generate_fake_wg_key() -> str:
    """
    Generates a fake but valid WireGuard key for testing purposes.

    Returns:
        A base64-encoded 32-byte string ending in '='.
    """
    return base64.b64encode(os.urandom(32)).decode("ascii")


@pytest.fixture
def tempdir():
    path = Path(tempfile.mkdtemp())
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_parse_sections_simple(tempdir):
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()

    f = tempdir / "config.conf"
    f.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24

        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = 0.0.0.0/0
        """
    )

    sections = _parse_sections(f)

    assert "Interface" in sections
    assert "Peer" in sections
    assert f"PrivateKey = {privkey}" in sections["Interface"][0]
    assert f"PublicKey = {pubkey}" in sections["Peer"][0]


def test_config_file_save_and_load(tempdir):
    config_file = tempdir / "wg0.conf"
    config = WireGuardInterfaceConfigFile(config_file)

    private_key = _generate_fake_wg_key()

    interface_entry = WireGuardInterfaceEntry(
        PrivateKey=private_key, Address="10.0.0.1/24", ListenPort="51820"
    )

    peer_key = _generate_fake_wg_key()
    peer_entry = WireGuardPeerEntry(PublicKey=peer_key, AllowedIPs="0.0.0.0/0")

    config.save(interface_entry, [peer_entry])
    loaded_interface, loaded_peers = config.load()

    assert loaded_interface.private_key == private_key
    assert loaded_interface.address == "10.0.0.1/24"
    assert loaded_interface.listen_port == "51820"
    assert len(loaded_peers) == 1
    assert loaded_peers[0].public_key == peer_key


def test_invalid_peer_missing_fields(tempdir):
    config_file = tempdir / "bad.conf"
    private_key = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {private_key}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        AllowedIPs = 0.0.0.0/0
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)

    with pytest.raises(ValueError, match=r"Missing required.*PublicKey"):
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

    with pytest.raises(ValueError, match=r"Missing required.*PrivateKey"):
        config.load()


def test_interface_end_to_end(tempdir):
    iface = WireGuardInterface(name="wg-test", config_dir=tempdir)
    privkey = _generate_fake_wg_key()
    entry = WireGuardInterfaceEntry(
        PrivateKey=f"{privkey}",
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    iface.set_interface(entry)
    pubkey = _generate_fake_wg_key()
    peer = WireGuardPeerEntry(PublicKey=f"{pubkey}", AllowedIPs="10.0.0.2/32")
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

    entry = WireGuardInterfaceEntry(
        PrivateKey=key,
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    iface = config.create_interface(
        name="wg0",
        interface_entry=entry,
    )

    assert iface.config_file.exists()

    all_ifaces = config.interfaces()
    assert len(all_ifaces) == 1
    assert all_ifaces[0].name == "wg0"

    config.remove_interface("wg0")
    assert not iface.config_file.exists()


def test_minimal_valid_config(tempdir):
    config_file = tempdir / "good.conf"
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = 0.0.0.0/0
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()

    assert interface_entry.private_key == privkey
    assert interface_entry.address == "10.0.0.1/24"
    assert interface_entry.listen_port == "51820"
    assert len(peer_entries) == 1
    assert peer_entries[0].public_key == pubkey


def test_blank_file(tempdir):
    config_file = tempdir / "blank.conf"
    config_file.write_text("")

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing \\[Interface\\] section"):
        config.load()


def test_missing_interface_section(tempdir):
    config_file = tempdir / "no_interface.conf"
    pubkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = 0.0.0.0/0
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing \\[Interface\\] section"):
        config.load()


def test_unknown_section(tempdir):
    config_file = tempdir / "unknown_section.conf"
    privkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
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
    privkey1 = _generate_fake_wg_key()
    privkey2 = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey1}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Interface]
        PrivateKey = {privkey2}
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
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = 0.0.0.0/0
        Foo = Bar
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Unknown field 'Foo' in \\[Peer\\] section"):
        config.load()


def test_peer_missing_allowed_ips(tempdir):
    config_file = tempdir / "missing_allowedips.conf"
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = {pubkey}
        """
    )

    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match=r"Missing required.*AllowedIPs"):
        config.load()


def test_line_outside_section_strict(tempdir):
    config_file = tempdir / "outside_line.conf"
    privkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        This is a bad line

        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820
        """
    )

    with pytest.raises(ValueError, match="Line outside any section"):
        _parse_sections(config_file, strict=True)


def test_line_outside_section_non_strict(tempdir):
    config_file = tempdir / "outside_line_nonstrict.conf"
    privkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        This is a bad line

        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820
        """
    )

    section_map = _parse_sections(config_file, strict=False)
    assert "Interface" in section_map
    assert any(f"PrivateKey = {privkey}" in l for l in section_map["Interface"])


def test_wireguard_interface_entry_from_lines_and_to_lines():
    privkey = _generate_fake_wg_key()
    lines = [
        f"PrivateKey = {privkey}",
        "Address = 10.0.0.1/24",
        "ListenPort = 51820",
    ]
    entry = WireGuardInterfaceEntry.from_lines(lines)
    assert entry.private_key == privkey
    assert entry.address == "10.0.0.1/24"
    assert entry.listen_port == "51820"

    output_lines = entry.to_lines()
    assert output_lines[0] == "[Interface]"
    assert f"PrivateKey = {privkey}" in output_lines
    assert "Address = 10.0.0.1/24" in output_lines
    assert "ListenPort = 51820" in output_lines


def test_wireguard_peer_entry_from_lines_and_to_lines():
    pubkey = _generate_fake_wg_key()
    lines = [
        f"PublicKey = {pubkey}",
        "AllowedIPs = 0.0.0.0/0",
        "Endpoint = example.com:51820",
    ]
    peer = WireGuardPeerEntry.from_lines(lines)
    assert peer.public_key == pubkey
    assert peer.allowed_ips == "0.0.0.0/0"
    assert peer.endpoint == "example.com:51820"

    output_lines = peer.to_lines()
    assert output_lines[0] == "[Peer]"
    assert f"PublicKey = {pubkey}" in output_lines
    assert "AllowedIPs = 0.0.0.0/0" in output_lines
    assert "Endpoint = example.com:51820" in output_lines


def test_interface_entry_validation_success():
    privkey = _generate_fake_wg_key()
    entry = WireGuardInterfaceEntry(
        PrivateKey=privkey,
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    entry.validate()


def test_peer_entry_validation_success():
    pubkey = _generate_fake_wg_key()
    peer = WireGuardPeerEntry(
        PublicKey=pubkey,
        AllowedIPs="0.0.0.0/0",
    )
    peer.validate()


def test_interface_entry_missing_private_key():
    entry = WireGuardInterfaceEntry(
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    with pytest.raises(ValueError, match=r"Missing required.*PrivateKey"):
        entry.validate()


def test_peer_entry_missing_public_key():
    peer = WireGuardPeerEntry(
        AllowedIPs="0.0.0.0/0",
    )
    with pytest.raises(ValueError, match=r"Missing required.*PublicKey"):
        peer.validate()


def test_peer_entry_unknown_field():
    pubkey = _generate_fake_wg_key()
    peer = WireGuardPeerEntry(
        PublicKey=pubkey,
        AllowedIPs="0.0.0.0/0",
        UnknownField="oops",
    )
    with pytest.raises(
        ValueError, match="Unknown field 'UnknownField' in \\[Peer\\] section"
    ):
        peer.validate()


def test_entries_equality():
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    entry1 = WireGuardInterfaceEntry(
        PrivateKey=privkey,
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    entry2 = WireGuardInterfaceEntry(
        PrivateKey=privkey,
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    assert entry1 == entry2

    peer1 = WireGuardPeerEntry(
        PublicKey=pubkey,
        AllowedIPs="0.0.0.0/0",
    )
    peer2 = WireGuardPeerEntry(
        PublicKey=pubkey,
        AllowedIPs="0.0.0.0/0",
    )
    assert peer1 == peer2


def test_entries_inequality_different_section():
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    entry = WireGuardInterfaceEntry(
        PrivateKey=privkey,
        Address="10.0.0.1/24",
        ListenPort="51820",
    )
    peer = WireGuardPeerEntry(
        PublicKey=pubkey,
        AllowedIPs="10.0.0.0/24",
    )
    assert entry != peer


def test_whitespace_in_section_name(tempdir):
    """Test that a section name with spaces is treated as missing [Interface]."""
    config_file = tempdir / "bad_section.conf"
    privkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [The Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    with pytest.raises(ValueError, match="Missing \\[Interface\\] section"):
        config.load()


def test_whitespace_in_key_or_value(tempdir):
    """Test that whitespace in key or value does not crash, but is preserved."""
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    config_file = tempdir / "whitespace_key_value.conf"
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey    =    {privkey}
        Address    =    10.0.0.1/24
        ListenPort   =   51820

        [Peer]
        PublicKey   =   {pubkey}
        AllowedIPs  =   0.0.0.0/0
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()

    assert interface_entry.private_key == privkey
    assert interface_entry.address == "10.0.0.1/24"
    assert interface_entry.listen_port == "51820"
    assert peer_entries[0].public_key == pubkey
    assert peer_entries[0].allowed_ips == "0.0.0.0/0"


def test_malformed_ip_in_interface(tempdir):
    config_file = tempdir / "bad_ip.conf"
    privkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = not_an_ip
        ListenPort = 51820
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()
    assert interface_entry.address == "not_an_ip"


def test_malformed_ip_in_peer(tempdir):
    config_file = tempdir / "bad_peer_ip.conf"
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = 999.999.999.999/99
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()
    assert peer_entries[0].allowed_ips == "999.999.999.999/99"


def test_malformed_endpoint_in_peer(tempdir):
    config_file = tempdir / "bad_endpoint.conf"
    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()
    config_file.write_text(
        f"""
        [Interface]
        PrivateKey = {privkey}
        Address = 10.0.0.1/24
        ListenPort = 51820

        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = 0.0.0.0/0
        Endpoint = 300.300.300.300:12345
        """
    )
    config = WireGuardInterfaceConfigFile(config_file)
    interface_entry, peer_entries = config.load()
    assert peer_entries[0].endpoint == "300.300.300.300:12345"


def test_wireguard_genkey(monkeypatch):
    def fake_run(*args, **kwargs):
        class Result:
            stdout = "testprivatekey\n"

        return Result()

    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/wg")
    monkeypatch.setattr("subprocess.run", fake_run)

    wg = WireGuard()
    key = wg.genkey()
    assert key == "testprivatekey"


def test_wireguard_pubkey(monkeypatch):
    def fake_run(*args, **kwargs):
        class Result:
            stdout = "testpublickey\n"

        return Result()

    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/wg")
    monkeypatch.setattr("subprocess.run", fake_run)

    wg = WireGuard()
    pubkey = wg.pubkey("dummy_private_key")
    assert pubkey == "testpublickey"


def test_save_interface_with_integer_field(tempdir):
    config_file = tempdir / "int_field.conf"
    config = WireGuardInterfaceConfigFile(config_file)

    privkey = _generate_fake_wg_key()
    entry = WireGuardInterfaceEntry(
        PrivateKey=privkey,
        Address="10.0.0.1/24",
        ListenPort=51820,  # Int instead of str is okay
    )

    config.save(entry, [])

    content = config_file.read_text()
    assert "ListenPort = 51820" in content


def test_save_peer_with_integer_keepalive(tempdir):
    config_file = tempdir / "peer_with_keepalive.conf"
    config = WireGuardInterfaceConfigFile(config_file)

    privkey = _generate_fake_wg_key()
    pubkey = _generate_fake_wg_key()

    interface = WireGuardInterfaceEntry(
        PrivateKey=privkey,
        Address="10.0.0.1/24",
    )
    peer = WireGuardPeerEntry(
        PublicKey=pubkey,
        AllowedIPs="0.0.0.0/0",
        PersistentKeepalive=25,  # ← Int value
    )

    config.save(interface, [peer])

    content = config_file.read_text()
    assert "PersistentKeepalive = 25" in content
