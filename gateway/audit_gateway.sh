#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PY="$HOME/.local/share/HomeAIAgent/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || command -v python)"
fi

cleanup_generated() {
  rm -rf ./__pycache__
  find . -maxdepth 2 -type f -name '*.pyc' -delete
}
trap cleanup_generated EXIT
cleanup_generated

export PYTHONDONTWRITEBYTECODE=1

echo "[AUDIT] Pre-commit static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[AUDIT] Python compile"
"$PY" -m py_compile ./*.py
cleanup_generated

echo "[AUDIT] Shell syntax"
SHELL_PARSER="$(command -v zsh || command -v bash)"
for f in ./*.sh; do
  "$SHELL_PARSER" -n "$f"
done

echo "[AUDIT] test_config_helpers.sh"
./test_config_helpers.sh

TESTS=(
  protocol_selftest.py
  test_audio_route_sticky.py
  test_info_cleanup_gateway_rpc.py
  test_info_ephemeral_cleanup.py
  test_info_stateless_user.py
  test_kitchen_audio.py
  test_kitchen_finish.py
  test_kitchen_menu.py
  test_kitchen_mic_probe.py
  test_kitchen_progress.py
  test_kitchen_qa.py
  test_kitchen_timer.py
  test_kitchen_voice_fastpath.py
  test_long_tts_segmentation.py
  test_multi_device_router.py
  test_networkspeaker_transport_profile.py
  test_notification_listener_offline.py
  test_openclaw_loopback_proxy_bypass.py
  test_runtime_artifact_paths.py
)

for f in "${TESTS[@]}"; do
  echo "[AUDIT] $f"
  "$PY" "$f"
done

cleanup_generated

echo "[AUDIT] Final static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[AUDIT] PASS: all offline release checks passed"
