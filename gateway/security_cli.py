#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_PERSISTENT_ENV = Path(
    os.getenv(
        "HOMEAI_CONFIG_FILE",
        str(Path.home() / ".config" / "HomeAIAgent" / "gateway.env"),
    )
).expanduser()
if _PERSISTENT_ENV.exists():
    load_dotenv(_PERSISTENT_ENV, override=False)

from security.device_auth import DeviceAuthManager, generate_device_secret


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _paths() -> tuple[Path, Path, Path]:
    config_dir = Path(os.getenv("HOMEAI_CONFIG_DIR", str(Path.home() / ".config" / "HomeAIAgent"))).expanduser()
    data_dir = Path(os.getenv("HOMEAI_DATA_DIR", str(Path.home() / ".local" / "share" / "HomeAIAgent"))).expanduser()
    security_dir = Path(os.getenv("HOMEAI_SECURITY_DIR", str(config_dir / "security"))).expanduser()
    registry = Path(os.getenv("HOMEAI_SECURITY_REGISTRY_FILE", str(security_dir / "devices.json"))).expanduser()
    secrets_file = Path(os.getenv("HOMEAI_SECURITY_SECRETS_FILE", str(security_dir / "device_secrets.json"))).expanduser()
    audit = Path(os.getenv("HOMEAI_SECURITY_AUDIT_LOG", str(data_dir / "security" / "security.log"))).expanduser()
    return registry, secrets_file, audit


def _read(path: Path, key: str) -> dict:
    if not path.exists():
        return {"version": 1, key: {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get(key, {}), dict):
        raise ValueError(f"invalid {path.name}")
    payload.setdefault("version", 1)
    payload.setdefault(key, {})
    return payload


def _write(path: Path, payload: dict, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)
    os.chmod(path, mode)


def _manager() -> DeviceAuthManager:
    registry, secrets_file, audit = _paths()
    return DeviceAuthManager(mode=os.getenv("HOMEAI_SECURITY_MODE", "observe"), registry_file=registry, secrets_file=secrets_file, audit_log=audit)


def cmd_init(_: argparse.Namespace) -> int:
    registry, secrets_file, _ = _paths()
    if not registry.exists():
        _write(registry, {"version": 1, "devices": {}}, 0o600)
    if not secrets_file.exists():
        _write(secrets_file, {"version": 1, "secrets": {}}, 0o600)
    print(f"[SECURITY] registry={registry}")
    print(f"[SECURITY] secrets={secrets_file}")
    print("[SECURITY] initialized; no device is trusted until explicitly paired")
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    registry, secrets_file, audit = _paths()
    reg = _read(registry, "devices")
    sec = _read(secrets_file, "secrets")
    device_id = args.device_id.strip()
    if not device_id:
        raise ValueError("device_id is empty")
    if device_id in reg["devices"] and not args.rotate:
        raise ValueError("device already exists; use rotate to issue a new secret")
    secret = generate_device_secret()
    scopes = sorted({scope.strip() for scope in args.scope if scope.strip()})
    reg["devices"][device_id] = {
        "enabled": True,
        "role": args.role,
        "scopes": scopes,
        "label": args.label.strip(),
        "updated_at": _now(),
    }
    sec["secrets"][device_id] = secret
    _write(registry, reg, 0o600)
    _write(secrets_file, sec, 0o600)
    manager = DeviceAuthManager(mode="observe", registry_file=registry, secrets_file=secrets_file, audit_log=audit)
    manager.audit("DEVICE_PAIRED", device_id=device_id, role=args.role)
    print(f"[SECURITY] paired device={device_id} role={args.role}")
    print("[SECURITY] device secret (shown once; provision it into this device only):")
    print(secret)
    return 0


def cmd_set_enabled(args: argparse.Namespace, enabled: bool) -> int:
    registry, secrets_file, audit = _paths()
    reg = _read(registry, "devices")
    device_id = args.device_id.strip()
    record = reg["devices"].get(device_id)
    if not isinstance(record, dict):
        raise ValueError("device not found")
    record["enabled"] = enabled
    record["updated_at"] = _now()
    _write(registry, reg, 0o600)
    manager = DeviceAuthManager(mode="observe", registry_file=registry, secrets_file=secrets_file, audit_log=audit)
    manager.audit("DEVICE_ENABLED" if enabled else "DEVICE_REVOKED", device_id=device_id)
    print(f"[SECURITY] device={device_id} enabled={str(enabled).lower()}")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    registry, secrets_file, audit = _paths()
    reg = _read(registry, "devices")
    if args.device_id not in reg["devices"]:
        raise ValueError("device not found")
    sec = _read(secrets_file, "secrets")
    secret = generate_device_secret()
    sec["secrets"][args.device_id] = secret
    reg["devices"][args.device_id]["updated_at"] = _now()
    _write(registry, reg, 0o600)
    _write(secrets_file, sec, 0o600)
    manager = DeviceAuthManager(mode="observe", registry_file=registry, secrets_file=secrets_file, audit_log=audit)
    manager.audit("DEVICE_SECRET_ROTATED", device_id=args.device_id)
    print(f"[SECURITY] rotated device={args.device_id}")
    print("[SECURITY] new device secret (shown once):")
    print(secret)
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    manager = _manager()
    summary = manager.startup_summary()
    print(f"mode={summary['mode']} registered={summary['registered']} enabled={summary['enabled']} disabled={summary['disabled']} secrets={summary['secrets']}")
    for device_id in sorted(manager.records):
        record = manager.records[device_id]
        secret_state = "yes" if device_id in manager.secrets else "NO"
        scopes = ",".join(record.scopes) if record.scopes else "-"
        print(f"{device_id}\tenabled={record.enabled}\trole={record.role}\tsecret={secret_state}\tscopes={scopes}")
    for warning in summary["warnings"]:
        print(f"WARN: {warning}")
    return 0



def _device_rows(manager: DeviceAuthManager) -> list[dict]:
    runtime = manager.runtime_snapshot()
    live = runtime.get("devices", {}) if isinstance(runtime, dict) else {}
    if not isinstance(live, dict):
        live = {}
    ids = sorted(set(manager.records) | set(live))
    rows: list[dict] = []
    for device_id in ids:
        record = manager.records.get(device_id)
        raw = live.get(device_id, {})
        rt = raw if isinstance(raw, dict) else {}
        registered = record is not None
        enabled = bool(record.enabled) if record is not None else False
        has_secret = device_id in manager.secrets
        connected = bool(rt.get("connected"))
        authenticated = bool(rt.get("authenticated"))
        last_auth_ok_at = str(rt.get("last_auth_ok_at") or "")
        if registered and not enabled:
            status = "REVOKED"
        elif not registered and connected:
            status = "UNKNOWN_OBSERVED"
        elif connected and authenticated:
            status = "AUTHENTICATED"
        elif connected and str(rt.get("state") or "") == "auth_failed":
            status = "AUTH_FAILED"
        elif connected and str(rt.get("state") or "") == "challenged":
            status = "CHALLENGED"
        elif connected:
            status = "OBSERVE"
        elif last_auth_ok_at:
            status = "OFFLINE_VERIFIED"
        elif registered:
            status = "REGISTERED_NOT_VERIFIED"
        else:
            status = "UNKNOWN_OBSERVED"
        rows.append({
            "device_id": device_id,
            "status": status,
            "registered": registered,
            "enabled": enabled if registered else None,
            "secret": has_secret,
            "connected": connected,
            "authenticated": authenticated,
            "role": (record.role if record is not None else str(rt.get("role") or "-")),
            "peer": str(rt.get("peer") or "-"),
            "last_verified": last_auth_ok_at or "-",
            "reason": str(rt.get("last_reason") or "-"),
        })
    return rows


def cmd_devices(args: argparse.Namespace) -> int:
    manager = _manager()
    rows = _device_rows(manager)
    if args.json:
        print(json.dumps({"mode": manager.mode, "devices": rows}, ensure_ascii=False, indent=2))
        return 0
    print(f"mode={manager.mode} devices={len(rows)}")
    if not rows:
        print("(no devices seen or registered yet)")
        return 0
    print("DEVICE_ID\tSTATUS\tROLE\tSECRET\tCONNECTED\tPEER\tLAST_VERIFIED\tREASON")
    for row in rows:
        print(
            f"{row['device_id']}\t{row['status']}\t{row['role']}\t"
            f"{'yes' if row['secret'] else 'NO'}\t"
            f"{'yes' if row['connected'] else 'no'}\t{row['peer']}\t"
            f"{row['last_verified']}\t{row['reason']}"
        )
    return 0

def cmd_status(_: argparse.Namespace) -> int:
    manager = _manager()
    summary = manager.startup_summary()
    ready, problems = manager.enforcement_ready()
    print(json.dumps({**summary, "device_enforcement_ready": ready, "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if ready else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HomeAIAgent device-security registry")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    pair = sub.add_parser("pair")
    pair.add_argument("device_id")
    pair.add_argument("--role", choices=("companion", "speaker", "kitchen"), default="companion")
    pair.add_argument("--scope", action="append", default=[])
    pair.add_argument("--label", default="")
    pair.add_argument("--rotate", action="store_true")
    for name in ("revoke", "enable", "rotate"):
        item = sub.add_parser(name)
        item.add_argument("device_id")
    sub.add_parser("list")
    devices = sub.add_parser("devices")
    devices.add_argument("--json", action="store_true")
    sub.add_parser("status")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "init":
            return cmd_init(args)
        if args.command == "pair":
            return cmd_pair(args)
        if args.command == "revoke":
            return cmd_set_enabled(args, False)
        if args.command == "enable":
            return cmd_set_enabled(args, True)
        if args.command == "rotate":
            return cmd_rotate(args)
        if args.command == "list":
            return cmd_list(args)
        if args.command == "devices":
            return cmd_devices(args)
        if args.command == "status":
            return cmd_status(args)
    except Exception as exc:
        print(f"[SECURITY-ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
