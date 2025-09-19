#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs

import os, sys, socket, re

sys.path.insert(0, "/sensos/lib")
from utils import setup_logging, read_kv_config, privileged_shell  # noqa: E402

NETWORK_CONF = "/sensos/etc/network.conf"
NFT_FAMILY = "inet"
NFT_TABLE_BASE = "sensos_base"  # always-on baseline
NFT_TABLE_UPLINK = "sensos_uplink"  # toggled by BANDWIDTH_POLICY
HOTSPOT_IF = os.environ.get("SENSOS_HOTSPOT_IF", "wlan0")
ALLOW_ICMP = True  # uplink ping/ICMPv4 while locked (ICMPv6 essentials always allowed)


def die(msg, rc=1):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(rc)


def resolve_ipv4(host_or_ip: str) -> str:
    try:
        socket.inet_aton(host_or_ip)
        return host_or_ip
    except OSError:
        pass
    info = socket.getaddrinfo(host_or_ip, None, socket.AF_INET, socket.SOCK_DGRAM)
    if not info:
        die(f"Failed to resolve IPv4 for {host_or_ip}")
    return info[0][4][0]


def egress_iface_for(dst_ip: str) -> str:
    out, rc = privileged_shell(f"ip route get {dst_ip}")
    if rc != 0 or not out:
        die(f"ip route get failed for {dst_ip}")
    m = re.search(r"\bdev\s+(\S+)", out)
    if not m:
        die(f"Could not determine egress interface for {dst_ip} (route: {out})")
    return m.group(1)


def iface_exists(ifname: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{ifname}")


def iface_is_up(ifname: str) -> bool:
    try:
        with open(f"/sys/class/net/{ifname}/operstate") as f:
            return f.read().strip() == "up"
    except Exception:
        return False


def choose_uplink_if(endpoint_ip: str, cfg: dict) -> str:
    # Priority: env override > config > auto-detect by route
    explicit = os.environ.get("SENSOS_UPLINK_IF") or cfg.get("UPLINK_IF")
    if explicit:
        if not iface_exists(explicit):
            die(f"UPLINK_IF '{explicit}' does not exist on this system")
        if not iface_is_up(explicit):
            print(
                f"⚠️ UPLINK_IF '{explicit}' is not UP yet; applying rules anyway.",
                file=sys.stderr,
            )
        return explicit
    # Fallback to auto detection
    auto = egress_iface_for(endpoint_ip)
    if not iface_exists(auto):
        die(f"Auto-detected uplink '{auto}' does not exist")
    if not iface_is_up(auto):
        print(
            f"⚠️ Auto-detected uplink '{auto}' is not UP yet; applying rules anyway.",
            file=sys.stderr,
        )
    return auto


def nft_table_exists(name: str) -> bool:
    _, rc = privileged_shell(f"nft list table {NFT_FAMILY} {name}", silent=True)
    return rc == 0


def ensure_baseline(wg_iface: str | None):
    """
    Always-on baseline:
    - Accept loopback, established/related, ICMP/ICMPv6
    - Accept all traffic arriving via the authenticated WG interface
    - Keep hotspot access (wlan0) for field access
    """
    wg_accept = f'iifname "{wg_iface}" accept' if wg_iface else ""
    rules = f"""
table {NFT_FAMILY} {NFT_TABLE_BASE} {{
  chain input {{
    type filter hook input priority 0;
    policy drop;

    iif lo accept
    ct state {{ established, related }} accept

    ip protocol icmp accept
    ip6 nexthdr icmpv6 accept

    # Allow traffic arriving over the authenticated WireGuard interface
    {wg_accept}

    # Field hotspot access (adjust as needed)
    iifname "{HOTSPOT_IF}" tcp dport {{ 22, 80, 443 }} accept
  }}

  chain forward {{
    type filter hook forward priority 0;
    policy drop;
  }}

  chain output {{
    type filter hook output priority 0;
    policy accept;  # egress is governed by the uplink table when restricted
  }}
}}
""".lstrip()
    if nft_table_exists(NFT_TABLE_BASE):
        privileged_shell(f"nft delete table {NFT_FAMILY} {NFT_TABLE_BASE}", silent=True)
    _, rc = privileged_shell("nft -f - <<'EOF'\n" + rules + "EOF\n")
    if rc != 0:
        die("Failed to apply baseline nftables rules")


def clear_uplink():
    if nft_table_exists(NFT_TABLE_UPLINK):
        privileged_shell(
            f"nft delete table {NFT_FAMILY} {NFT_TABLE_UPLINK}", silent=True
        )


def apply_uplink(endpoint_ip: str, endpoint_port: str, eg_if: str):
    """
    Strict WG-only on chosen uplink, but allow DHCPv4/6 and ICMPv6 so the link can obtain addresses.
    Optional ICMPv4 allowed when ALLOW_ICMP=True.
    """
    icmp_v4_allow = (
        "icmp type { echo-request, echo-reply, destination-unreachable, time-exceeded } accept"
        if ALLOW_ICMP
        else ""
    )
    icmp_v6_allow = "ip6 nexthdr icmpv6 accept"  # SLAAC/ND/etc.

    rules = f"""
table {NFT_FAMILY} {NFT_TABLE_UPLINK} {{
  set wg_endpoint_ip {{
    type ipv4_addr
    flags constant
    elements = {{ {endpoint_ip} }}
  }}

  chain input {{
    type filter hook input priority 0;
    counter

    # WireGuard FROM server
    iifname "{eg_if}" udp sport {endpoint_port} ip saddr @wg_endpoint_ip accept

    # Bootstrap: DHCPv4/v6 server->client, ICMPv6 essentials, optional ICMPv4
    iifname "{eg_if}" udp sport 67  udp dport 68  accept    # DHCPv4
    iifname "{eg_if}" udp sport 547 udp dport 546 accept    # DHCPv6
    iifname "{eg_if}" {icmp_v6_allow}
    {f'iifname \"{eg_if}\" ' + icmp_v4_allow if ALLOW_ICMP else ''}

    # Drop the rest on uplink
    iifname "{eg_if}" drop
  }}

  chain output {{
    type filter hook output priority 0;
    counter

    # WireGuard TO server
    oifname "{eg_if}" udp dport {endpoint_port} ip daddr @wg_endpoint_ip accept

    # Bootstrap: DHCPv4/v6 client->server, ICMPv6 essentials, optional ICMPv4
    oifname "{eg_if}" udp sport 68  udp dport 67  accept    # DHCPv4
    oifname "{eg_if}" udp sport 546 udp dport 547 accept    # DHCPv6
    oifname "{eg_if}" {icmp_v6_allow}
    {f'oifname \"{eg_if}\" ' + icmp_v4_allow if ALLOW_ICMP else ''}

    # Drop the rest on uplink
    oifname "{eg_if}" drop
  }}
}}
""".lstrip()
    clear_uplink()
    _, rc = privileged_shell("nft -f - <<'EOF'\n" + rules + "EOF\n")
    if rc != 0:
        die("Failed to apply uplink nftables rules")


def main():
    setup_logging()  # /sensos/log/sensos-apply-bandwidth-policy.log

    cfg = read_kv_config(NETWORK_CONF)
    wg_iface = cfg.get("NETWORK_NAME") if cfg else None

    # Always install baseline first (keeps WG and hotspot reachable)
    ensure_baseline(wg_iface)

    if not cfg:
        print(
            f"⚠️ {NETWORK_CONF} not found or empty; baseline is active, skipping uplink enforcement."
        )
        return

    policy = cfg.get("BANDWIDTH_POLICY", "unrestricted").lower()
    endpoint_host = cfg.get("WG_ENDPOINT_IP")
    if not endpoint_host:
        print(
            "⚠️ WG_ENDPOINT_IP not set; baseline is active, skipping uplink enforcement."
        )
        clear_uplink()
        return

    endpoint_port = cfg.get("WG_ENDPOINT_PORT", "51820")
    if not endpoint_port.isdigit():
        die(f"Invalid WG_ENDPOINT_PORT: {endpoint_port}")

    endpoint_ip = resolve_ipv4(endpoint_host)

    # Choose uplink deterministically if provided, else auto-detect
    eg_if = choose_uplink_if(endpoint_ip, cfg)

    # Safety: avoid clamping the hotspot unless explicitly allowed
    if eg_if == HOTSPOT_IF and os.environ.get("SENSOS_ENFORCE_ON_HOTSPOT") != "1":
        print(
            f"⚠️ Chosen uplink is {eg_if} (hotspot). Leaving baseline only to avoid lockout."
        )
        clear_uplink()
        return

    if policy == "restricted":
        apply_uplink(endpoint_ip, endpoint_port, eg_if)
        print(
            f"✅ Restricted: WG-only on {eg_if} to {endpoint_ip}:{endpoint_port}. Baseline remains active."
        )
    else:
        clear_uplink()
        print("✅ Unrestricted: baseline active; uplink clamp removed.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        die(str(e))
