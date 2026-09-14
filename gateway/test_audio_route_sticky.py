"""Static regression guard for R5 NetworkSpeaker sticky routing."""
from pathlib import Path

text = (Path(__file__).with_name("companion_gateway.py")).read_text(encoding="utf-8")
required = [
    "[AUDIO-ROUTE-HOLD]",
    "[AUDIO-ROUTE-RECOVER]",
    "local fallback suppressed to prevent mid-reply speaker switching",
    "_wait_for_bound_speaker_reconnect",
    "playback_completed_segments",
    "AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS",
    "_send_network_pcm_with_recovery",
]
missing = [token for token in required if token not in text]
if missing:
    raise SystemExit(f"FAIL missing sticky-route guards: {missing}")
print("PASS NetworkSpeaker sticky-route guards present")
