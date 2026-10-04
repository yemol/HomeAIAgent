#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
echo "[A6.1-AUDIT] Agent auth source contract"
python3 scripts/test_agent_auth_contract.py
echo "[A6.1-AUDIT] Gateway A6 regression suite"
(cd gateway && ./audit_a6_security.sh)
echo "[A6.1-AUDIT] source secret scan"
if grep -RIE --exclude-dir=.git --exclude-dir=__MACOSX --exclude='*.zip' --exclude='*.md' \
  'HOMEAI_DEVICE_SECRET[[:space:]]*=|deviceAuthSecret[[:space:]]*=[[:space:]]*"[A-Za-z0-9_-]{32,}"' \
  src include gateway scripts >/dev/null 2>&1; then
  echo "[A6.1-AUDIT] FAIL: possible compiled device secret detected" >&2
  exit 1
fi
echo "[A6.1-AUDIT] PASS"
