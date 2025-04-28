# test.py

import pytest
import asyncio
import ipaddress
import psycopg
import socket
import docker
import core

from unittest import mock
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPBasicCredentials

from core import (
    lifespan,
    generate_default_ip_range,
    resolve_hostname,
    search_for_next_available_ip,
    get_network_details,
    insert_peer,
    restart_container,
    get_container_ip,
    get_db,
    restart_container,
    get_container_ip,
    authenticate,
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


@mock.patch("core.get_db")
def test_get_network_details_found(mock_get_db):
    fake_cursor = mock.MagicMock()
    fake_cursor.fetchone.return_value = (1, "10.0.0.0/16", "pubkey", "10.0.0.1", 51820)
    mock_get_db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        fake_cursor
    )

    result = get_network_details("network1")
    assert result == (1, "10.0.0.0/16", "pubkey", "10.0.0.1", 51820)


@mock.patch("core.get_db")
def test_insert_peer_success(mock_get_db):
    fake_cursor = mock.MagicMock()
    fake_cursor.fetchone.return_value = (123, "some-uuid")
    mock_get_db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        fake_cursor
    )

    result = insert_peer(1, "10.0.0.2", note="test note")
    assert result == (123, "some-uuid")


@mock.patch("core.docker.from_env")
def test_restart_container_success(mock_from_env):
    fake_container = mock.MagicMock()
    fake_container.status = "exited"
    mock_client = mock.MagicMock()
    mock_client.containers.get.return_value = fake_container
    mock_from_env.return_value = mock_client

    restart_container("dummy-container")
    fake_container.restart.assert_called_once()


@mock.patch("core.docker.from_env")
def test_get_container_ip_success(mock_from_env):
    fake_container = mock.MagicMock()
    fake_container.attrs = {
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2"}}}
    }
    mock_client = mock.MagicMock()
    mock_client.containers.get.return_value = fake_container
    mock_from_env.return_value = mock_client

    ip = get_container_ip("dummy-container")
    assert ip == "172.17.0.2"


@mock.patch("core.docker.from_env", side_effect=Exception("Docker error"))
def test_get_container_ip_failure(mock_from_env):
    ip = get_container_ip("dummy-container")
    assert ip is None


@pytest.mark.asyncio
@mock.patch("core.get_db")
async def test_lifespan_startup_and_shutdown(mock_get_db):
    # Mock all database calls inside get_db()
    fake_cursor = mock.MagicMock()
    mock_get_db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        fake_cursor
    )

    app = FastAPI()

    async with lifespan(app):
        pass  # During lifespan, setup should happen.

    # Verify that some key operations were attempted (optional)
    assert fake_cursor.execute.call_count > 0


@mock.patch("core.psycopg.connect")
def test_get_db_retries_and_fails(mock_connect):
    # Make psycopg.connect raise OperationalError every time
    mock_connect.side_effect = psycopg.OperationalError()

    with pytest.raises(psycopg.OperationalError):
        get_db(retries=3, delay=0)  # Use short retries for test speed

    assert mock_connect.call_count == 3


@mock.patch("core.docker.from_env")
def test_restart_container_error(mock_from_env):
    # Setup: docker.from_env().containers.get() will raise an exception
    mock_client = mock.MagicMock()
    mock_client.containers.get.side_effect = Exception("Container not found")
    mock_from_env.return_value = mock_client

    # Should not raise, but should log error internally
    restart_container("fake_container")

    assert mock_client.containers.get.called


@mock.patch("core.docker.from_env")
def test_get_container_ip_error(mock_from_env):
    # Setup: docker.from_env().containers.get() will raise an exception
    mock_client = mock.MagicMock()
    mock_client.containers.get.side_effect = Exception("No such container")
    mock_from_env.return_value = mock_client

    ip = get_container_ip("missing_container")

    assert ip is None
    assert mock_client.containers.get.called


@mock.patch("core.docker.from_env")
def test_restart_container_success(mock_from_env):
    # Setup: docker.from_env().containers.get() returns a running container
    mock_container = mock.MagicMock()
    mock_container.status = "running"
    mock_client = mock.MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_from_env.return_value = mock_client

    restart_container("running_container")

    assert mock_client.containers.get.called
    assert mock_container.restart.called


@mock.patch("core.docker.from_env")
def test_get_container_ip_success(mock_from_env):
    # Setup: container has an IP address
    mock_container = mock.MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2"}}}
    }
    mock_client = mock.MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_from_env.return_value = mock_client

    ip = get_container_ip("existing_container")

    assert ip == "172.17.0.2"
    assert mock_client.containers.get.called


@mock.patch("core.get_db")
def test_insert_peer_success(mock_get_db):
    # Setup mock cursor/connection
    mock_cur = mock.MagicMock()
    mock_cur.fetchone.return_value = (1, "some-uuid")
    mock_conn = mock.MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_get_db.return_value.__enter__.return_value = mock_conn

    result = insert_peer(network_id=1, wg_ip="10.0.0.2", note="test peer")

    assert result == (1, "some-uuid")
    mock_cur.execute.assert_called_once()
    mock_cur.fetchone.assert_called_once()


@mock.patch("core.get_db")
def test_get_network_details_success(mock_get_db):
    # Setup mock cursor/connection
    mock_cur = mock.MagicMock()
    mock_cur.fetchone.return_value = (1, "10.0.0.0/16", "pubkey", "10.0.0.1", 51820)
    mock_conn = mock.MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_get_db.return_value.__enter__.return_value = mock_conn

    result = get_network_details("test_network")

    assert result == (1, "10.0.0.0/16", "pubkey", "10.0.0.1", 51820)
    mock_cur.execute.assert_called_once()
    mock_cur.fetchone.assert_called_once()


def test_restart_container_running():
    mock_client = mock.MagicMock()
    mock_container = mock.MagicMock()
    mock_container.status = "running"
    mock_client.containers.get.return_value = mock_container

    with mock.patch("docker.from_env", return_value=mock_client):
        restart_container("test-container")

    mock_container.restart.assert_called_once()


def test_restart_container_not_running():
    mock_client = mock.MagicMock()
    mock_container = mock.MagicMock()
    mock_container.status = "exited"
    mock_client.containers.get.return_value = mock_container

    with mock.patch("docker.from_env", return_value=mock_client):
        restart_container("test-container")

    mock_container.restart.assert_called_once()


def test_restart_container_error():
    mock_client = mock.MagicMock()
    mock_client.containers.get.side_effect = Exception("Container not found")

    with mock.patch("docker.from_env", return_value=mock_client):
        restart_container("missing-container")


def test_get_container_ip_success():
    mock_client = mock.MagicMock()
    mock_container = mock.MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2"}}}
    }
    mock_client.containers.get.return_value = mock_container

    with mock.patch("docker.from_env", return_value=mock_client):
        ip = get_container_ip("test-container")

    assert ip == "172.17.0.2"


def test_get_container_ip_no_ip():
    mock_client = mock.MagicMock()
    mock_container = mock.MagicMock()
    mock_container.attrs = {
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": ""}}}
    }
    mock_client.containers.get.return_value = mock_container

    with mock.patch("docker.from_env", return_value=mock_client):
        ip = get_container_ip("test-container")

    assert ip is None


def test_get_container_ip_error():
    mock_client = mock.MagicMock()
    mock_client.containers.get.side_effect = Exception("Container not found")

    with mock.patch("docker.from_env", return_value=mock_client):
        ip = get_container_ip("missing-container")

    assert ip is None


def test_authenticate_success(monkeypatch):
    monkeypatch.setattr(core, "API_PASSWORD", "secret")
    credentials = HTTPBasicCredentials(username="any", password="secret")
    assert core.authenticate(credentials) == credentials


def test_authenticate_failure(monkeypatch):
    monkeypatch.setattr(core, "API_PASSWORD", "secret")
    credentials = HTTPBasicCredentials(username="any", password="wrongpassword")
    with pytest.raises(HTTPException) as exc_info:
        core.authenticate(credentials)
    assert exc_info.value.status_code == 401
