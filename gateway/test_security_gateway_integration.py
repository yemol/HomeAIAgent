#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from security.device_auth import AuthContext, DeviceAuthManager, build_auth_proof, generate_device_secret


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_code = None
        self.close_reason = ""
        self.remote_address = ("10.20.30.40", 4444)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


def load_gateway():
    path = Path(__file__).with_name("companion_gateway.py")
    name = "homeai_gateway_security_integration_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


async def main() -> None:
    g = load_gateway()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = root / "devices.json"
        secrets_file = root / "device_secrets.json"
        audit = root / "security.log"
        secret = generate_device_secret()
        write_json(registry, {
            "version": 1,
            "devices": {
                "speaker-test-01": {
                    "enabled": True,
                    "role": "speaker",
                    "scopes": ["audio.receive", "volume.write"],
                }
            },
        })
        write_json(secrets_file, {
            "version": 1,
            "secrets": {"speaker-test-01": secret},
        })
        g.DEVICE_AUTH = DeviceAuthManager(
            mode="enforce",
            registry_file=registry,
            secrets_file=secrets_file,
            audit_log=audit,
        )

        ws = FakeWS()
        assert g._security_peer_label(ws) == "10.20.30.40"
        source = Path(__file__).with_name("companion_gateway.py").read_text(encoding="utf-8")
        assert "session.device_role != old_role" in source
        assert "session.parent_device_id != old_parent" in source
        session = g.ClientSession(ws=ws)
        g._apply_device_hello(session, {
            "type": "device.hello",
            "device_id": "speaker-test-01",
            "device_role": "speaker",
            "parent_device_id": g.HOMEAI_PRIMARY_DEVICE_ID,
            "capabilities": {"volume_control": True},
        })
        assert await g._security_begin_ws_session(session, role="speaker")
        assert ws.sent[-1]["type"] == "security.challenge"
        assert not g.DEVICE_AUTH.should_allow_application(session.security)

        challenge = ws.sent[-1]
        client_nonce = "integration-client-nonce-1234"
        proof = build_auth_proof(
            secret,
            device_id="speaker-test-01",
            challenge_id=challenge["challenge_id"],
            server_nonce=challenge["server_nonce"],
            client_nonce=client_nonce,
        )
        ok = await g._security_verify_ws_session(session, {
            "type": "security.auth",
            "device_id": "speaker-test-01",
            "challenge_id": challenge["challenge_id"],
            "client_nonce": client_nonce,
            "proof": proof,
        })
        assert ok
        assert session.security.authenticated
        assert ws.sent[-1]["type"] == "security.auth.ok"

        await g._finish_device_hello(session)
        assert any(item.get("type") == "gateway.ready" for item in ws.sent)
        ready = next(item for item in ws.sent if item.get("type") == "gateway.ready")
        assert ready["security"]["authenticated"] is True
        assert not ws.closed

        bad_ws = FakeWS()
        bad_session = g.ClientSession(ws=bad_ws)
        g._apply_device_hello(bad_session, {
            "type": "device.hello",
            "device_id": "speaker-test-01",
            "device_role": "speaker",
            "parent_device_id": g.HOMEAI_PRIMARY_DEVICE_ID,
        })
        assert await g._security_begin_ws_session(bad_session, role="speaker")
        bad_challenge = bad_ws.sent[-1]
        bad_ok = await g._security_verify_ws_session(bad_session, {
            "type": "security.auth",
            "device_id": "speaker-test-01",
            "challenge_id": bad_challenge["challenge_id"],
            "client_nonce": "bad-client-nonce-12345",
            "proof": "Y" * 43,
        })
        assert not bad_ok
        assert bad_ws.closed and bad_ws.close_code == 1008

    print("HomeAIAgent A6.0 Gateway security integration: PASS")


asyncio.run(main())
