#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from security.device_auth import AuthContext, DeviceAuthManager, build_auth_proof, generate_device_secret
from security_cli import _device_rows


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    registry = root / "devices.json"
    secrets_file = root / "device_secrets.json"
    audit = root / "security.log"
    runtime = root / "device_status.json"
    secret = generate_device_secret()
    write_json(registry, {
        "version": 1,
        "devices": {
            "homeai-agent-main-01": {
                "enabled": True,
                "role": "companion",
                "scopes": ["agent.query"],
            }
        },
    })
    write_json(secrets_file, {
        "version": 1,
        "secrets": {"homeai-agent-main-01": secret},
    })

    manager = DeviceAuthManager(
        mode="observe",
        registry_file=registry,
        secrets_file=secrets_file,
        audit_log=audit,
        runtime_file=runtime,
    )
    manager.mark_gateway_start()

    # Registered device is challenged first.
    ctx = AuthContext()
    outcome = manager.begin(
        ctx,
        device_id="homeai-agent-main-01",
        role="companion",
        peer="192.168.1.20",
    )
    assert outcome["status"] == "challenge"
    rows = {row["device_id"]: row for row in _device_rows(manager)}
    assert rows["homeai-agent-main-01"]["status"] == "CHALLENGED"

    challenge = outcome["challenge"]
    client_nonce = "agent-client-nonce-0123456789"
    proof = build_auth_proof(
        secret,
        device_id="homeai-agent-main-01",
        challenge_id=challenge["challenge_id"],
        server_nonce=challenge["server_nonce"],
        client_nonce=client_nonce,
    )
    ok, reason = manager.verify(ctx, {
        "device_id": "homeai-agent-main-01",
        "challenge_id": challenge["challenge_id"],
        "client_nonce": client_nonce,
        "proof": proof,
    }, peer="192.168.1.20")
    assert ok and reason == "ok"
    rows = {row["device_id"]: row for row in _device_rows(manager)}
    row = rows["homeai-agent-main-01"]
    assert row["status"] == "AUTHENTICATED"
    assert row["connected"] is True
    assert row["last_verified"] != "-"

    # Disconnect preserves proof that this identity has verified successfully before.
    manager.mark_disconnected(ctx)
    rows = {row["device_id"]: row for row in _device_rows(manager)}
    row = rows["homeai-agent-main-01"]
    assert row["status"] == "OFFLINE_VERIFIED"
    assert row["connected"] is False
    assert row["last_verified"] != "-"

    # A6.0.3: a stale socket disconnect must not overwrite a newer reconnect.
    old_ctx = AuthContext()
    old_outcome = manager.begin(
        old_ctx,
        device_id="homeai-agent-main-01",
        role="companion",
        peer="192.168.1.20",
    )
    old_challenge = old_outcome["challenge"]
    old_nonce = "old-client-nonce-0123456789"
    old_proof = build_auth_proof(
        secret,
        device_id="homeai-agent-main-01",
        challenge_id=old_challenge["challenge_id"],
        server_nonce=old_challenge["server_nonce"],
        client_nonce=old_nonce,
    )
    assert manager.verify(old_ctx, {
        "device_id": "homeai-agent-main-01",
        "challenge_id": old_challenge["challenge_id"],
        "client_nonce": old_nonce,
        "proof": old_proof,
    }, peer="192.168.1.20")[0]

    new_ctx = AuthContext()
    new_outcome = manager.begin(
        new_ctx,
        device_id="homeai-agent-main-01",
        role="companion",
        peer="192.168.1.20",
    )
    new_challenge = new_outcome["challenge"]
    new_nonce = "new-client-nonce-0123456789"
    new_proof = build_auth_proof(
        secret,
        device_id="homeai-agent-main-01",
        challenge_id=new_challenge["challenge_id"],
        server_nonce=new_challenge["server_nonce"],
        client_nonce=new_nonce,
    )
    assert manager.verify(new_ctx, {
        "device_id": "homeai-agent-main-01",
        "challenge_id": new_challenge["challenge_id"],
        "client_nonce": new_nonce,
        "proof": new_proof,
    }, peer="192.168.1.20")[0]

    manager.mark_disconnected(old_ctx)
    rows = {row["device_id"]: row for row in _device_rows(manager)}
    row = rows["homeai-agent-main-01"]
    assert row["status"] == "AUTHENTICATED"
    assert row["connected"] is True

    manager.mark_disconnected(new_ctx)
    rows = {row["device_id"]: row for row in _device_rows(manager)}
    row = rows["homeai-agent-main-01"]
    assert row["status"] == "OFFLINE_VERIFIED"
    assert row["connected"] is False

    # An unregistered legacy/rogue client remains visible in observe mode.
    rogue = AuthContext()
    allowed = manager.begin(
        rogue,
        device_id="unknown-test-01",
        role="companion",
        peer="192.168.1.99",
    )
    assert allowed["status"] == "allowed"
    rows = {row["device_id"]: row for row in _device_rows(manager)}
    assert rows["unknown-test-01"]["status"] == "UNKNOWN_OBSERVED"

    # No secret material may be written to runtime visibility data.
    runtime_text = runtime.read_text(encoding="utf-8")
    assert secret not in runtime_text
    assert proof not in runtime_text

print("HomeAIAgent A6.0.3 security device-status/reconnect-race tests: PASS")
