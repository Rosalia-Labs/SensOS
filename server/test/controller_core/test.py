# test.py

import ipaddress
import socket
import pytest
from unittest import mock

from core import (
    generate_default_ip_range,
    resolve_hostname,
    search_for_next_available_ip,
)


def test_generate_default_ip_range():
    assert generate_default_ip_range("network1").subnet_of(
        ipaddress.ip_network("10.0.0.0/8")
    )
    assert generate_default_ip_range("different") != generate_default_ip_range(
        "network1"
    )


def test_resolve_hostname_ip_direct():
    assert resolve_hostname("8.8.8.8") == "8.8.8.8"
    assert resolve_hostname("::1") == "::1"  # IPv6 localhost


def test_resolve_hostname_dns(monkeypatch):
    def fake_getaddrinfo(host, port, family):
        return [(socket.AF_INET, None, None, None, ("93.184.216.34", None))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_hostname("example.com") == "93.184.216.34"


def test_resolve_hostname_invalid(monkeypatch):
    def fake_getaddrinfo(host, port, family):
        raise socket.gaierror()

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_hostname("invalid.hostname.") is None


@mock.patch("core.get_assigned_ips", return_value=set())
def test_search_for_next_available_ip_empty(mock_get_assigned):
    ip = search_for_next_available_ip("10.0.0.0/24", network_id=1)
    assert str(ip).startswith("10.0.0.")


@mock.patch(
    "core.get_assigned_ips",
    return_value={ipaddress.ip_address(f"10.0.0.{i}") for i in range(1, 255)},
)
def test_search_for_next_available_ip_full(mock_get_assigned):
    ip = search_for_next_available_ip("10.0.0.0/24", network_id=1)
    assert ip is None
