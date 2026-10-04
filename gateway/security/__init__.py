"""HomeAIAgent device-authentication primitives."""

from .device_auth import (
    AuthContext,
    DeviceAuthManager,
    DeviceRecord,
    build_auth_proof,
    generate_device_secret,
)

__all__ = [
    "AuthContext",
    "DeviceAuthManager",
    "DeviceRecord",
    "build_auth_proof",
    "generate_device_secret",
]
