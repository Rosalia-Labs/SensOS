# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class RegisterPeerRequest(BaseModel):
    network_name: str
    subnet_offset: int = 0
    note: Optional[str] = None


class RegisterWireguardKeyRequest(BaseModel):
    wg_ip: str
    wg_public_key: str


class RegisterSSHKeyRequest(BaseModel):
    wg_ip: str
    username: str
    uid: int
    ssh_public_key: str
    key_type: str
    key_size: int
    key_comment: Optional[str] = None
    fingerprint: str
    expires_at: Optional[datetime] = None


class ClientStatusRequest(BaseModel):
    hostname: str
    uptime_seconds: int
    disk_available_gb: float
    memory_used_mb: int
    memory_total_mb: int
    load_1m: float
    load_5m: Optional[float] = None
    load_15m: Optional[float] = None
    version: str
    status_message: Optional[str] = None


class HardwareProfile(BaseModel):
    wg_ip: str
    hostname: str
    model: str
    kernel_version: str
    cpu: dict
    firmware: dict
    memory: dict
    disks: dict
    usb_devices: str
    network_interfaces: dict


class LocationUpdateRequest(BaseModel):
    wg_ip: str
    latitude: float
    longitude: float
