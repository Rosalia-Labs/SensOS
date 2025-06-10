import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient
from unittest import mock
import api

from api import (
    router,
    RegisterPeerRequest,
    RegisterWireguardKeyRequest,
    RegisterSSHKeyRequest,
    ClientStatusRequest,
    LocationUpdateRequest,
    HardwareProfile,
)


@pytest.fixture
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_dashboard_success(monkeypatch, client):
    # Mock DB response
    fake_version = (
        1,
        0,
        0,
        None,
        "abcdef",
        "main",
        "v1.0.0",
        "false",
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:00Z",  # 🛠️ Add 10th field (timestamp again or whatever)
    )
    fake_networks = [("network1", "10.0.0.0/16", "1.2.3.4", 51820)]

    def fake_cursor_execute(query, *args, **kwargs):
        if "FROM sensos.version_history" in query:
            fake_cursor.fetchone.return_value = fake_version
        elif "FROM sensos.networks" in query:
            fake_cursor.fetchall.return_value = fake_networks

    fake_cursor = mock.MagicMock()
    fake_cursor.execute.side_effect = fake_cursor_execute

    monkeypatch.setattr(
        api,
        "get_db",
        lambda: mock.MagicMock(
            __enter__=lambda s: mock.MagicMock(
                cursor=lambda: mock.MagicMock(__enter__=lambda s: fake_cursor)
            )
        ),
    )

    # ✅ Correct way to override authenticate
    client.app.dependency_overrides[api.authenticate] = lambda: HTTPBasicCredentials(
        username="test", password="test"
    )

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sensor Network Manager" in resp.text


def test_create_network_invalid_port(client):
    monkeypatch_auth(client)
    resp = client.post(
        "/create-network",
        data={"name": "test", "wg_public_ip": "1.2.3.4", "wg_port": "99999"},
    )
    assert resp.status_code == 400
    assert "Invalid WireGuard port" in resp.text


def monkeypatch_auth(client):
    # Helper to bypass authentication for testclient
    client.app.dependency_overrides[api.authenticate] = lambda: HTTPBasicCredentials(
        username="test", password="test"
    )


def test_register_peer_invalid_subnet(monkeypatch, client):
    monkeypatch_auth(client)

    # Mock get_network_details
    monkeypatch.setattr(
        api,
        "get_network_details",
        lambda name: (1, "10.0.0.0/16", "pubkey", "1.2.3.4", 51820),
    )

    req = {"network_name": "test", "subnet_offset": 9999}
    resp = client.post("/register-peer", json=req)
    assert resp.status_code == 400


def test_register_wireguard_key_not_found(monkeypatch, client):
    monkeypatch_auth(client)

    monkeypatch.setattr(api, "register_wireguard_key_in_db", lambda wg_ip, pubkey: None)

    req = {"wg_ip": "10.0.0.2", "wg_public_key": "dummy"}
    resp = client.post("/register-wireguard-key", json=req)
    assert resp.status_code == 404


def test_client_status_success(monkeypatch, client):
    monkeypatch_auth(client)

    monkeypatch.setattr(
        api,
        "get_db",
        lambda: mock.MagicMock(
            __enter__=lambda s: mock.MagicMock(
                cursor=lambda: mock.MagicMock(__enter__=lambda s: mock.MagicMock())
            )
        ),
    )

    payload = {
        "hostname": "test-client",
        "uptime_seconds": 12345,
        "disk_available_gb": 10.5,
        "memory_used_mb": 512,
        "memory_total_mb": 1024,
        "load_1m": 0.25,
        "version": "1.0.0",
        "wireguard_ip": "10.0.0.2",
    }
    resp = client.post("/client-status", json=payload)
    assert resp.status_code == 200


def test_upload_hardware_profile_not_found(monkeypatch, client):
    monkeypatch_auth(client)

    monkeypatch.setattr(
        api,
        "get_db",
        lambda: mock.MagicMock(
            __enter__=lambda s: mock.MagicMock(
                cursor=lambda: mock.MagicMock(
                    __enter__=lambda s: mock.MagicMock(fetchone=lambda: None)
                )
            )
        ),
    )

    payload = {
        "wg_ip": "10.0.0.2",
        "hostname": "test",
        "model": "model",
        "kernel_version": "1.0",
        "cpu": {},
        "firmware": {},
        "memory": {},
        "disks": {},
        "usb_devices": "none",
        "network_interfaces": {},
    }
    resp = client.post("/upload-hardware-profile", json=payload)
    assert resp.status_code == 404
