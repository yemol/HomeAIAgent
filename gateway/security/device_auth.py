from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTH_PROTOCOL = "homeai-auth/1"
VALID_MODES = frozenset({"off", "observe", "enforce"})
DEFAULT_CHALLENGE_TTL_SEC = 10.0
DEFAULT_FAILURE_LIMIT = 5
DEFAULT_FAILURE_WINDOW_SEC = 60.0
DEFAULT_BLOCK_SEC = 300.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty base64url value")
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + padding)


def generate_device_secret() -> str:
    """Return a new 256-bit device secret encoded as URL-safe base64."""
    return _b64url_encode(secrets.token_bytes(32))


def _auth_message(
    device_id: str,
    challenge_id: str,
    server_nonce: str,
    client_nonce: str,
) -> bytes:
    fields = (
        AUTH_PROTOCOL,
        str(device_id or "").strip(),
        str(challenge_id or "").strip(),
        str(server_nonce or "").strip(),
        str(client_nonce or "").strip(),
    )
    return ("\n".join(fields)).encode("utf-8")


def build_auth_proof(
    secret: str,
    *,
    device_id: str,
    challenge_id: str,
    server_nonce: str,
    client_nonce: str,
) -> str:
    key = _b64url_decode(secret)
    if len(key) < 32:
        raise ValueError("device secret must contain at least 256 bits")
    digest = hmac.new(
        key,
        _auth_message(device_id, challenge_id, server_nonce, client_nonce),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


@dataclass(frozen=True)
class DeviceRecord:
    device_id: str
    enabled: bool = True
    role: str = "companion"
    scopes: tuple[str, ...] = ()
    label: str = ""


@dataclass
class AuthContext:
    state: str = "new"  # new | bypass | observe | challenged | authenticated | denied
    # A6.0.3: unique per-WebSocket lease. Runtime status updates carry this id so
    # a stale/old socket cannot mark a newer authenticated reconnect offline.
    connection_id: str = field(default_factory=lambda: secrets.token_hex(12))
    device_id: str = ""
    role: str = ""
    peer: str = ""
    challenge_id: str = ""
    server_nonce: str = ""
    challenge_expires_at: float = 0.0
    challenge_used: bool = False
    authenticated_at: float = 0.0
    scopes: set[str] = field(default_factory=set)
    ready_sent: bool = False
    reason: str = ""

    @property
    def authenticated(self) -> bool:
        return self.state in {"authenticated", "bypass"}


class DeviceAuthManager:
    """Gateway-side HomeAgent device authentication.

    Secrets never travel over the network. A registered device proves possession
    of its per-device secret with HMAC-SHA256 over a one-time server challenge.
    "observe" mode is deliberately migration-safe: authentication is measured and
    logged, but unauthenticated legacy clients are not blocked.
    """

    def __init__(
        self,
        *,
        mode: str,
        registry_file: Path,
        secrets_file: Path,
        audit_log: Path,
        runtime_file: Path | None = None,
        challenge_ttl_sec: float = DEFAULT_CHALLENGE_TTL_SEC,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
        failure_window_sec: float = DEFAULT_FAILURE_WINDOW_SEC,
        block_sec: float = DEFAULT_BLOCK_SEC,
    ) -> None:
        normalized = str(mode or "observe").strip().lower()
        if normalized not in VALID_MODES:
            normalized = "observe"
        self.mode = normalized
        self.registry_file = Path(registry_file).expanduser()
        self.secrets_file = Path(secrets_file).expanduser()
        self.audit_log = Path(audit_log).expanduser()
        self.runtime_file = (
            Path(runtime_file).expanduser()
            if runtime_file is not None
            else self.audit_log.parent / "device_status.json"
        )
        self.challenge_ttl_sec = max(3.0, min(60.0, float(challenge_ttl_sec)))
        self.failure_limit = max(2, int(failure_limit))
        self.failure_window_sec = max(10.0, float(failure_window_sec))
        self.block_sec = max(30.0, float(block_sec))
        self.records: dict[str, DeviceRecord] = {}
        self.secrets: dict[str, str] = {}
        self.load_warnings: list[str] = []
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._audit_throttle: dict[str, float] = {}
        self.reload()

    @classmethod
    def from_environment(
        cls,
        *,
        config_dir: Path,
        data_dir: Path,
    ) -> "DeviceAuthManager":
        security_dir = Path(
            os.getenv("HOMEAI_SECURITY_DIR", str(Path(config_dir) / "security"))
        ).expanduser()
        audit_dir = Path(
            os.getenv("HOMEAI_SECURITY_DATA_DIR", str(Path(data_dir) / "security"))
        ).expanduser()
        return cls(
            mode=os.getenv("HOMEAI_SECURITY_MODE", "observe"),
            registry_file=Path(
                os.getenv("HOMEAI_SECURITY_REGISTRY_FILE", str(security_dir / "devices.json"))
            ).expanduser(),
            secrets_file=Path(
                os.getenv("HOMEAI_SECURITY_SECRETS_FILE", str(security_dir / "device_secrets.json"))
            ).expanduser(),
            audit_log=Path(
                os.getenv("HOMEAI_SECURITY_AUDIT_LOG", str(audit_dir / "security.log"))
            ).expanduser(),
            runtime_file=Path(
                os.getenv("HOMEAI_SECURITY_RUNTIME_FILE", str(audit_dir / "device_status.json"))
            ).expanduser(),
            challenge_ttl_sec=float(os.getenv("HOMEAI_SECURITY_CHALLENGE_TTL_SEC", "10")),
            failure_limit=int(os.getenv("HOMEAI_SECURITY_FAILURE_LIMIT", "5")),
            failure_window_sec=float(os.getenv("HOMEAI_SECURITY_FAILURE_WINDOW_SEC", "60")),
            block_sec=float(os.getenv("HOMEAI_SECURITY_BLOCK_SEC", "300")),
        )

    def reload(self) -> None:
        self.records = {}
        self.secrets = {}
        self.load_warnings = []
        self._load_registry()
        self._load_secrets()

    def _load_registry(self) -> None:
        if not self.registry_file.exists():
            self.load_warnings.append(f"registry missing: {self.registry_file}")
            return
        try:
            payload = json.loads(self.registry_file.read_text(encoding="utf-8"))
            devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
            if not isinstance(devices, dict):
                raise ValueError("devices must be an object")
            for raw_id, raw_record in devices.items():
                device_id = str(raw_id or "").strip()
                if not device_id or not isinstance(raw_record, dict):
                    continue
                scopes_raw = raw_record.get("scopes", [])
                scopes = tuple(
                    sorted({str(item).strip() for item in scopes_raw if str(item).strip()})
                ) if isinstance(scopes_raw, list) else ()
                self.records[device_id] = DeviceRecord(
                    device_id=device_id,
                    enabled=bool(raw_record.get("enabled", True)),
                    role=str(raw_record.get("role") or "companion").strip().lower(),
                    scopes=scopes,
                    label=str(raw_record.get("label") or "").strip(),
                )
        except Exception as exc:
            self.load_warnings.append(
                f"registry load failed: {type(exc).__name__}: {exc}"
            )

    def _load_secrets(self) -> None:
        if not self.secrets_file.exists():
            self.load_warnings.append(f"secrets missing: {self.secrets_file}")
            return
        try:
            mode_bits = self.secrets_file.stat().st_mode & 0o077
            if mode_bits:
                self.load_warnings.append(
                    f"secrets permissions too broad: {oct(self.secrets_file.stat().st_mode & 0o777)}"
                )
            payload = json.loads(self.secrets_file.read_text(encoding="utf-8"))
            secrets_map = payload.get("secrets", {}) if isinstance(payload, dict) else {}
            if not isinstance(secrets_map, dict):
                raise ValueError("secrets must be an object")
            for raw_id, raw_secret in secrets_map.items():
                device_id = str(raw_id or "").strip()
                secret = str(raw_secret or "").strip()
                if not device_id or not secret:
                    continue
                decoded = _b64url_decode(secret)
                if len(decoded) < 32:
                    raise ValueError(f"secret too short for {device_id}")
                self.secrets[device_id] = secret
        except Exception as exc:
            self.load_warnings.append(
                f"secrets load failed: {type(exc).__name__}: {exc}"
            )
            self.secrets = {}


    def _runtime_load(self) -> dict[str, Any]:
        if not self.runtime_file.exists():
            return {"version": 1, "devices": {}}
        try:
            payload = json.loads(self.runtime_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("runtime status must be an object")
            devices = payload.get("devices", {})
            if not isinstance(devices, dict):
                raise ValueError("runtime devices must be an object")
            payload.setdefault("version", 1)
            payload.setdefault("devices", {})
            return payload
        except Exception:
            # Runtime visibility must never break device authentication itself.
            return {"version": 1, "devices": {}}

    def _runtime_write(self, payload: dict[str, Any]) -> None:
        try:
            self.runtime_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.runtime_file.with_suffix(self.runtime_file.suffix + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(tmp, 0o600)
            tmp.replace(self.runtime_file)
            os.chmod(self.runtime_file, 0o600)
        except OSError:
            pass

    def runtime_snapshot(self) -> dict[str, Any]:
        return self._runtime_load()

    def _runtime_mark(
        self,
        *,
        device_id: str,
        role: str = "",
        peer: str = "",
        connected: bool | None = None,
        state: str = "",
        authenticated: bool | None = None,
        reason: str = "",
        auth_ok: bool = False,
        connection_id: str = "",
    ) -> None:
        did = str(device_id or "").strip()
        if not did:
            return
        payload = self._runtime_load()
        devices = payload.setdefault("devices", {})
        raw = devices.get(did, {})
        item = dict(raw) if isinstance(raw, dict) else {}
        now = _utc_now()
        item["device_id"] = did
        item["last_seen_at"] = now
        if connection_id:
            item["connection_id"] = str(connection_id)[:64]
        if role:
            item["role"] = str(role)[:32]
        if peer:
            item["peer"] = str(peer)[:128]
        if connected is not None:
            item["connected"] = bool(connected)
        if state:
            item["state"] = str(state)[:48]
        if authenticated is not None:
            item["authenticated"] = bool(authenticated)
        if reason:
            item["last_reason"] = str(reason)[:240]
        if auth_ok:
            item["last_auth_ok_at"] = now
            item["last_reason"] = "ok"
        devices[did] = item
        self._runtime_write(payload)

    def mark_gateway_start(self) -> None:
        payload = self._runtime_load()
        now = _utc_now()
        payload["gateway_started_at"] = now
        devices = payload.setdefault("devices", {})
        for raw in devices.values():
            if not isinstance(raw, dict):
                continue
            raw["connected"] = False
            raw["authenticated"] = False
            raw["state"] = "offline"
        self._runtime_write(payload)

    def mark_disconnected(self, context: AuthContext, *, reason: str = "disconnect") -> None:
        # A6.0.3 reconnect race guard: one logical device may briefly have an
        # old socket and a newly authenticated socket at the same time. Only the
        # connection that currently owns the runtime lease may mark it offline.
        payload = self._runtime_load()
        devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
        raw = devices.get(context.device_id, {}) if isinstance(devices, dict) else {}
        current_connection_id = str(raw.get("connection_id") or "") if isinstance(raw, dict) else ""
        if (
            current_connection_id
            and context.connection_id
            and current_connection_id != context.connection_id
        ):
            self.audit(
                "AUTH_STALE_DISCONNECT_IGNORED",
                device_id=context.device_id,
                peer=context.peer,
                role=context.role,
                reason="newer_connection_active",
            )
            return
        self._runtime_mark(
            device_id=context.device_id,
            role=context.role,
            peer=context.peer,
            connected=False,
            state="offline",
            authenticated=False,
            reason=reason,
            connection_id=context.connection_id,
        )

    def startup_summary(self) -> dict[str, Any]:
        enabled = sum(1 for record in self.records.values() if record.enabled)
        disabled = len(self.records) - enabled
        return {
            "mode": self.mode,
            "registered": len(self.records),
            "enabled": enabled,
            "disabled": disabled,
            "secrets": len(self.secrets),
            "warnings": list(self.load_warnings),
        }

    def enforcement_ready(self) -> tuple[bool, list[str]]:
        problems = list(self.load_warnings)
        enabled_ids = {
            device_id for device_id, record in self.records.items() if record.enabled
        }
        missing = sorted(device_id for device_id in enabled_ids if device_id not in self.secrets)
        if missing:
            problems.append("enabled devices missing secrets: " + ", ".join(missing))
        if not enabled_ids:
            problems.append("no enabled devices registered")
        return (not problems, problems)

    def record_for(self, device_id: str) -> DeviceRecord | None:
        return self.records.get(str(device_id or "").strip())

    def is_scope_allowed(self, context: AuthContext, scope: str) -> bool:
        if self.mode == "off":
            return True
        if not context.authenticated:
            return self.mode == "observe"
        requested = str(scope or "").strip()
        return requested in context.scopes or "*" in context.scopes

    def _rate_key(self, peer: str, device_id: str) -> str:
        return f"{str(peer or '-').strip()}|{str(device_id or '-').strip()}"

    def _prune_failures(self, now: float) -> None:
        cutoff = now - self.failure_window_sec
        for key in list(self._failures):
            recent = [stamp for stamp in self._failures[key] if stamp >= cutoff]
            if recent:
                self._failures[key] = recent
            else:
                self._failures.pop(key, None)
        for key in list(self._blocked_until):
            if self._blocked_until[key] <= now:
                self._blocked_until.pop(key, None)
        if len(self._failures) > 512:
            for key in list(self._failures)[: len(self._failures) - 512]:
                self._failures.pop(key, None)

    def is_rate_limited(self, peer: str, device_id: str) -> bool:
        now = time.time()
        self._prune_failures(now)
        return self._blocked_until.get(self._rate_key(peer, device_id), 0.0) > now

    def _record_failure(self, peer: str, device_id: str) -> None:
        now = time.time()
        self._prune_failures(now)
        key = self._rate_key(peer, device_id)
        history = self._failures.setdefault(key, [])
        history.append(now)
        if len(history) >= self.failure_limit:
            self._blocked_until[key] = now + self.block_sec
            self.audit(
                "AUTH_RATE_LIMIT",
                device_id=device_id,
                peer=peer,
                reason=f"failures={len(history)} block_sec={int(self.block_sec)}",
            )

    def begin(
        self,
        context: AuthContext,
        *,
        device_id: str,
        role: str,
        peer: str,
    ) -> dict[str, Any]:
        did = str(device_id or "").strip()
        device_role = str(role or "").strip().lower()
        context.device_id = did
        context.role = device_role
        context.peer = str(peer or "").strip()
        context.reason = ""

        if self.mode == "off":
            context.state = "bypass"
            context.authenticated_at = time.time()
            context.scopes = {"*"}
            self.audit("AUTH_BYPASS", device_id=did, peer=peer, role=device_role)
            self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state="bypass", authenticated=True, reason="security_off", auth_ok=True, connection_id=context.connection_id)
            return {"status": "allowed", "challenge": None, "reason": "security_off"}

        record = self.record_for(did)
        if record is None:
            context.state = "observe" if self.mode == "observe" else "denied"
            context.reason = "unknown_device"
            self.audit("AUTH_UNKNOWN_DEVICE", device_id=did, peer=peer, role=device_role)
            self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state=context.state, authenticated=False, reason=context.reason, connection_id=context.connection_id)
            return {
                "status": "allowed" if self.mode == "observe" else "denied",
                "challenge": None,
                "reason": context.reason,
            }

        if not record.enabled:
            context.state = "denied"
            context.reason = "device_disabled"
            self.audit("AUTH_DEVICE_DISABLED", device_id=did, peer=peer, role=device_role)
            self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state="denied", authenticated=False, reason=context.reason, connection_id=context.connection_id)
            return {"status": "denied", "challenge": None, "reason": context.reason}

        if record.role and record.role != device_role:
            context.state = "observe" if self.mode == "observe" else "denied"
            context.reason = "role_mismatch"
            self.audit(
                "AUTH_ROLE_MISMATCH",
                device_id=did,
                peer=peer,
                role=device_role,
                reason=f"expected={record.role}",
            )
            self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state=context.state, authenticated=False, reason=context.reason, connection_id=context.connection_id)
            return {
                "status": "allowed" if self.mode == "observe" else "denied",
                "challenge": None,
                "reason": context.reason,
            }

        if did not in self.secrets:
            context.state = "observe" if self.mode == "observe" else "denied"
            context.reason = "secret_missing"
            self.audit("AUTH_SECRET_MISSING", device_id=did, peer=peer, role=device_role)
            self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state=context.state, authenticated=False, reason=context.reason, connection_id=context.connection_id)
            return {
                "status": "allowed" if self.mode == "observe" else "denied",
                "challenge": None,
                "reason": context.reason,
            }

        if self.is_rate_limited(peer, did):
            context.state = "denied" if self.mode == "enforce" else "observe"
            context.reason = "rate_limited"
            self.audit("AUTH_BLOCKED", device_id=did, peer=peer, role=device_role)
            self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state=context.state, authenticated=False, reason=context.reason, connection_id=context.connection_id)
            return {
                "status": "denied" if self.mode == "enforce" else "allowed",
                "challenge": None,
                "reason": context.reason,
            }

        context.state = "challenged"
        context.challenge_id = secrets.token_hex(16)
        context.server_nonce = _b64url_encode(secrets.token_bytes(24))
        context.challenge_expires_at = time.time() + self.challenge_ttl_sec
        context.challenge_used = False
        context.scopes = set(record.scopes)
        self.audit("AUTH_CHALLENGE", device_id=did, peer=peer, role=device_role)
        self._runtime_mark(device_id=did, role=device_role, peer=peer, connected=True, state="challenged", authenticated=False, reason="registered_device", connection_id=context.connection_id)
        return {
            "status": "challenge",
            "reason": "registered_device",
            "challenge": {
                "type": "security.challenge",
                "protocol": AUTH_PROTOCOL,
                "device_id": did,
                "challenge_id": context.challenge_id,
                "server_nonce": context.server_nonce,
                "expires_in_ms": int(self.challenge_ttl_sec * 1000),
                "algorithm": "HMAC-SHA256",
            },
        }

    def verify(
        self,
        context: AuthContext,
        payload: dict[str, Any],
        *,
        peer: str,
    ) -> tuple[bool, str]:
        did = str(payload.get("device_id") or "").strip()
        challenge_id = str(payload.get("challenge_id") or "").strip()
        client_nonce = str(payload.get("client_nonce") or "").strip()
        proof = str(payload.get("proof") or "").strip()

        if context.state != "challenged":
            self._record_failure(peer, did or context.device_id)
            self.audit("AUTH_FAIL", device_id=did or context.device_id, peer=peer, reason="no_active_challenge")
            self._runtime_mark(device_id=did or context.device_id, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="no_active_challenge", connection_id=context.connection_id)
            return False, "no_active_challenge"
        if context.challenge_used:
            self._record_failure(peer, context.device_id)
            self.audit("AUTH_FAIL", device_id=context.device_id, peer=peer, reason="challenge_replayed")
            self._runtime_mark(device_id=context.device_id, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="challenge_replayed", connection_id=context.connection_id)
            return False, "challenge_replayed"
        context.challenge_used = True
        if time.time() > context.challenge_expires_at:
            self._record_failure(peer, context.device_id)
            self.audit("AUTH_FAIL", device_id=context.device_id, peer=peer, reason="challenge_expired")
            self._runtime_mark(device_id=context.device_id, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="challenge_expired", connection_id=context.connection_id)
            return False, "challenge_expired"
        if did != context.device_id or challenge_id != context.challenge_id:
            self._record_failure(peer, did or context.device_id)
            self.audit("AUTH_FAIL", device_id=did or context.device_id, peer=peer, reason="challenge_mismatch")
            self._runtime_mark(device_id=did or context.device_id, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="challenge_mismatch", connection_id=context.connection_id)
            return False, "challenge_mismatch"
        if len(client_nonce) < 16 or len(client_nonce) > 128 or len(proof) < 32:
            self._record_failure(peer, did)
            self.audit("AUTH_FAIL", device_id=did, peer=peer, reason="malformed_proof")
            self._runtime_mark(device_id=did, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="malformed_proof", connection_id=context.connection_id)
            return False, "malformed_proof"

        secret = self.secrets.get(did)
        if not secret:
            self._record_failure(peer, did)
            self.audit("AUTH_FAIL", device_id=did, peer=peer, reason="secret_missing")
            self._runtime_mark(device_id=did, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="secret_missing", connection_id=context.connection_id)
            return False, "secret_missing"

        try:
            expected = build_auth_proof(
                secret,
                device_id=did,
                challenge_id=context.challenge_id,
                server_nonce=context.server_nonce,
                client_nonce=client_nonce,
            )
        except Exception:
            self._record_failure(peer, did)
            self.audit("AUTH_FAIL", device_id=did, peer=peer, reason="proof_build_failed")
            self._runtime_mark(device_id=did, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="proof_build_failed", connection_id=context.connection_id)
            return False, "proof_build_failed"

        if not hmac.compare_digest(expected, proof):
            self._record_failure(peer, did)
            self.audit("AUTH_FAIL", device_id=did, peer=peer, reason="bad_proof")
            self._runtime_mark(device_id=did, role=context.role, peer=peer, connected=True, state="auth_failed", authenticated=False, reason="bad_proof", connection_id=context.connection_id)
            return False, "bad_proof"

        context.state = "authenticated"
        context.authenticated_at = time.time()
        context.reason = "ok"
        self._failures.pop(self._rate_key(peer, did), None)
        self._blocked_until.pop(self._rate_key(peer, did), None)
        self.audit("AUTH_OK", device_id=did, peer=peer, role=context.role)
        self._runtime_mark(device_id=did, role=context.role, peer=peer, connected=True, state="authenticated", authenticated=True, reason="ok", auth_ok=True, connection_id=context.connection_id)
        return True, "ok"

    def should_allow_application(self, context: AuthContext) -> bool:
        if self.mode == "off":
            return True
        if context.authenticated:
            return True
        return self.mode == "observe" and context.state != "denied"

    def audit(
        self,
        event: str,
        *,
        device_id: str = "",
        peer: str = "",
        role: str = "",
        reason: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "ts": _utc_now(),
            "event": str(event or "SECURITY"),
            "mode": self.mode,
        }
        if device_id:
            item["device_id"] = str(device_id)[:128]
        if peer:
            item["peer"] = str(peer)[:128]
        if role:
            item["role"] = str(role)[:32]
        if reason:
            item["reason"] = str(reason)[:240]
        if extra:
            for key, value in extra.items():
                if key.lower() in {"secret", "proof", "token", "session_key"}:
                    continue
                item[str(key)[:64]] = value
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            created = not self.audit_log.exists()
            with self.audit_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            if created:
                os.chmod(self.audit_log, 0o600)
        except OSError:
            # Security logging must never crash the voice service. Enforce-mode
            # readiness is checked separately during Gateway preflight.
            pass

    def audit_throttled(
        self,
        key: str,
        event: str,
        *,
        every_sec: float = 300.0,
        **kwargs: Any,
    ) -> None:
        now = time.time()
        throttle_key = str(key or event)
        if self._audit_throttle.get(throttle_key, 0.0) > now:
            return
        self._audit_throttle[throttle_key] = now + max(1.0, float(every_sec))
        self.audit(event, **kwargs)
