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

echo "[AUDIT] Pre-commit static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[AUDIT] Python compile"
"$PY" -m compileall -q .
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
  test_security_auth.py
  test_security_gateway_integration.py
  test_audio_route_sticky.py
  test_followup_context.py
  test_followup_gateway_integration.py
  test_info_cleanup_gateway_rpc.py
  test_info_ephemeral_cleanup.py
  test_info_stateless_user.py
  test_kitchen_audio.py
  test_kitchen_finish.py
  test_kitchen_food.py
  test_kitchen_menu.py
  test_kitchen_mic_probe.py
  test_kitchen_progress.py
  test_kitchen_prep_migration.py
  test_kitchen_prep_required.py
  test_kitchen_unified_prep.py
  test_kitchen_qa.py
  test_kitchen_timer.py
  test_kitchen_timer_ui_r22.py
  test_kitchen_timer_dom_r23.py
  test_kitchen_http_state_scope_r24.py
  test_kitchen_r25_ui.py
  test_kitchen_r26_finish_ui.py
  test_kitchen_r27_finish_style.py
  test_kitchen_food_photo_scan_r28.py
  test_kitchen_r29_navigation_timer_popup.py
  test_kitchen_r31_picker_two_step.py
  test_kitchen_r36_obsidian_today_menu.py
  test_kitchen_r37_ui_fixes.py
  test_kitchen_standalone_timer.py
  test_kitchen_voice_fastpath.py
  test_long_tts_segmentation.py
  test_multi_device_router.py
  test_terminal_domain_routing.py
  test_networkspeaker_transport_profile.py
  test_notification_listener_offline.py
  test_reminder_device_routing.py
  test_openclaw_loopback_proxy_bypass.py
  test_runtime_artifact_paths.py
)

for f in "${TESTS[@]}"; do
  if [[ "$f" = "test_followup_gateway_integration.py" && ! -f ../src/main.cpp ]]; then
    echo "[AUDIT] SKIP $f (companion firmware ../src/main.cpp not included in Gateway-only package)"
    continue
  fi
  echo "[AUDIT] $f"
  "$PY" "$f"
done

cleanup_generated

echo "[AUDIT] Final static cleanliness"
"$PY" ./precommit_static_audit.py

echo "[AUDIT] PASS: all offline release checks passed"
