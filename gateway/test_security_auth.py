#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from security.device_auth import AuthContext, DeviceAuthManager, build_auth_proof, generate_device_secret


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


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
            },
            "disabled-test-01": {
                "enabled": False,
                "role": "companion",
                "scopes": ["agent.query"],
            },
        },
    })
    write_json(secrets_file, {
        "version": 1,
        "secrets": {
            "speaker-test-01": secret,
            "disabled-test-01": generate_device_secret(),
        },
    })

    manager = DeviceAuthManager(
        mode="enforce",
        registry_file=registry,
        secrets_file=secrets_file,
        audit_log=audit,
        failure_limit=3,
    )
    ready, problems = manager.enforcement_ready()
    assert ready, problems

    ctx = AuthContext()
    outcome = manager.begin(ctx, device_id="speaker-test-01", role="speaker", peer="10.0.0.2:1234")
    challenge = outcome["challenge"]
    assert outcome["status"] == "challenge"
    assert challenge["protocol"] == "homeai-auth/1"
    client_nonce = "client-nonce-0123456789"
    proof = build_auth_proof(
        secret,
        device_id="speaker-test-01",
        challenge_id=challenge["challenge_id"],
        server_nonce=challenge["server_nonce"],
        client_nonce=client_nonce,
    )
    ok, reason = manager.verify(ctx, {
        "device_id": "speaker-test-01",
        "challenge_id": challenge["challenge_id"],
        "client_nonce": client_nonce,
        "proof": proof,
    }, peer="10.0.0.2:1234")
    assert ok and reason == "ok"
    assert ctx.authenticated
    assert manager.is_scope_allowed(ctx, "audio.receive")
    assert not manager.is_scope_allowed(ctx, "inventory.write")

    replay_ok, replay_reason = manager.verify(ctx, {
        "device_id": "speaker-test-01",
        "challenge_id": challenge["challenge_id"],
        "client_nonce": client_nonce,
        "proof": proof,
    }, peer="10.0.0.2:1234")
    assert not replay_ok
    assert replay_reason == "no_active_challenge"

    bad_ctx = AuthContext()
    bad = manager.begin(bad_ctx, device_id="speaker-test-01", role="speaker", peer="10.0.0.3:1234")
    bad_ok, bad_reason = manager.verify(bad_ctx, {
        "device_id": "speaker-test-01",
        "challenge_id": bad["challenge"]["challenge_id"],
        "client_nonce": "another-client-nonce",
        "proof": "X" * 43,
    }, peer="10.0.0.3:1234")
    assert not bad_ok and bad_reason == "bad_proof"

    unknown_ctx = AuthContext()
    unknown = manager.begin(unknown_ctx, device_id="rogue-01", role="companion", peer="10.0.0.9:9000")
    assert unknown["status"] == "denied"
    assert not manager.should_allow_application(unknown_ctx)

    disabled_ctx = AuthContext()
    disabled = manager.begin(disabled_ctx, device_id="disabled-test-01", role="companion", peer="10.0.0.8:9000")
    assert disabled["status"] == "denied"

    observe = DeviceAuthManager(
        mode="observe",
        registry_file=registry,
        secrets_file=secrets_file,
        audit_log=audit,
    )
    legacy_ctx = AuthContext()
    legacy = observe.begin(legacy_ctx, device_id="legacy-unknown", role="companion", peer="10.0.0.7:9000")
    assert legacy["status"] == "allowed"
    assert observe.should_allow_application(legacy_ctx)
    assert not legacy_ctx.authenticated

    # Explicit revocation stays effective even during migration-safe observe mode.
    disabled_observe_ctx = AuthContext()
    disabled_observe = observe.begin(
        disabled_observe_ctx,
        device_id="disabled-test-01",
        role="companion",
        peer="10.0.0.6:9000",
    )
    assert disabled_observe["status"] == "denied"
    assert not observe.should_allow_application(disabled_observe_ctx)

    audit_text = audit.read_text(encoding="utf-8")
    assert "AUTH_OK" in audit_text
    assert "AUTH_FAIL" in audit_text
    assert secret not in audit_text
    assert proof not in audit_text

print("HomeAIAgent A6.0 device-auth unit tests: PASS")
