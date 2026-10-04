#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PY="$HOME/.local/share/HomeAIAgent/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || command -v python)"
fi

cleanup_generated() {
  find . -type d -name '__pycache__' -prune -exec rm -rf {} +
  find . -type f -name '*.pyc' -delete
}
trap cleanup_generated EXIT
cleanup_generated
export PYTHONDONTWRITEBYTECODE=1

echo "[A6-AUDIT] static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[A6-AUDIT] compile Gateway + security modules"
"$PY" -m compileall -q .
cleanup_generated

echo "[A6-AUDIT] security primitives"
"$PY" ./test_security_auth.py
"$PY" ./test_security_gateway_integration.py
"$PY" ./test_security_status.py

echo "[A6-AUDIT] existing device/audio/follow-up regressions"
"$PY" ./test_multi_device_router.py
"$PY" ./test_terminal_domain_routing.py
"$PY" ./test_networkspeaker_transport_profile.py
"$PY" ./test_audio_route_sticky.py
"$PY" ./test_followup_context.py
if [[ -f ../src/main.cpp ]]; then
  "$PY" ./test_followup_gateway_integration.py
else
  echo "[A6-AUDIT] SKIP test_followup_gateway_integration.py (companion firmware ../src/main.cpp not included in Gateway-only package)"
fi

echo "[A6-AUDIT] Kitchen R50.10 regression guards"
"$PY" ./test_kitchen_r50_8_inventory_intake_filter.py
"$PY" ./test_kitchen_r50_9_intake_identity_fix.py
"$PY" ./test_kitchen_r50_10_shopping_identity_match.py

cleanup_generated
echo "[A6-AUDIT] final static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[A6-AUDIT] PASS"
