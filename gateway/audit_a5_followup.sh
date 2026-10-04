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

echo "[A5-AUDIT] static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[A5-AUDIT] compile all Gateway Python modules"
"$PY" -m compileall -q .
cleanup_generated

echo "[A5-AUDIT] Follow-up state + Context Judge"
"$PY" ./test_followup_context.py
"$PY" ./test_followup_gateway_integration.py

echo "[A5-AUDIT] existing voice/audio/device paths"
"$PY" ./protocol_selftest.py
"$PY" ./test_audio_route_sticky.py
"$PY" ./test_long_tts_segmentation.py
"$PY" ./test_multi_device_router.py
"$PY" ./test_networkspeaker_transport_profile.py
"$PY" ./test_notification_listener_offline.py
"$PY" ./test_reminder_device_routing.py
"$PY" ./test_openclaw_loopback_proxy_bypass.py

echo "[A5-AUDIT] OpenClaw transient-session isolation"
"$PY" ./test_info_cleanup_gateway_rpc.py
"$PY" ./test_info_ephemeral_cleanup.py
"$PY" ./test_info_stateless_user.py

echo "[A5-AUDIT] current Kitchen R50 regression guards"
"$PY" ./test_kitchen_r50_8_inventory_intake_filter.py
"$PY" ./test_kitchen_r50_9_intake_identity_fix.py
"$PY" ./test_kitchen_r50_10_shopping_identity_match.py

echo "[A5-AUDIT] final static cleanliness"
cleanup_generated
"$PY" ./precommit_static_audit.py

echo "[A5-AUDIT] PASS"
