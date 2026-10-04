#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "src/main.cpp").read_text(encoding="utf-8")
config = (ROOT / "include/app_config.h").read_text(encoding="utf-8")

checks = {
    "explicit main device id": '#define HOMEAI_DEVICE_ID "homeai-agent-main-01"' in config,
    "companion role": '#define HOMEAI_DEVICE_ROLE "companion"' in config,
    "auth protocol": '#define HOMEAI_AUTH_PROTOCOL "homeai-auth/1"' in config,
    "separate auth NVS": 'kDeviceAuthNamespace[] = "homeai_auth"' in main,
    "challenge handler": '"security.challenge"' in main and 'sendDeviceAuthResponse(doc)' in main,
    "HMAC-SHA256": 'mbedtls_md_hmac' in main and 'MBEDTLS_MD_SHA256' in main,
    "fresh client nonce": 'esp_fill_random(nonceBytes' in main,
    "explicit hello identity": 'doc["device_id"] = HOMEAI_DEVICE_ID;' in main,
    "auth response identity": 'reply["device_id"] = HOMEAI_DEVICE_ID;' in main,
    "serial auth show": 'serialConfigLine == "AUTH SHOW"' in main,
    "serial auth set": 'serialConfigLine.startsWith("AUTH SET ")' in main,
    "serial auth clear": 'serialConfigLine == "AUTH CLEAR"' in main,
    "success ack": '"security.auth.ok"' in main,
    "failure ack": '"security.auth.failed"' in main,
    "deny ack": '"security.denied"' in main,
}

# Ensure the HMAC message field order matches gateway/security/device_auth.py:
# protocol, device_id, challenge_id, server_nonce, client_nonce, separated by LF.
order_pattern = re.compile(
    r'message \+= HOMEAI_AUTH_PROTOCOL;.*?message \+= \'\\n\';.*?'
    r'message \+= HOMEAI_DEVICE_ID;.*?message \+= \'\\n\';.*?'
    r'message \+= challengeId;.*?message \+= \'\\n\';.*?'
    r'message \+= serverNonce;.*?message \+= \'\\n\';.*?'
    r'message \+= clientNonce;',
    re.S,
)
checks["gateway-compatible HMAC field order"] = bool(order_pattern.search(main))

# No 43-character base64url production secret should appear as a literal in source/config.
secret_literal = re.compile(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])')
checks["no embedded device secret"] = not secret_literal.search(main + "\n" + config)
checks["secret never printed"] = 'Serial.println(deviceAuthSecret)' not in main and 'Serial.printf("%s", deviceAuthSecret' not in main

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
if failed:
    print("Agent auth contract FAILED: " + ", ".join(failed), file=sys.stderr)
    raise SystemExit(1)
print("HomeAIAgent A6.1 Agent auth contract: PASS")
