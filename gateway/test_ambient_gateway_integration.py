#!/usr/bin/env python3
"""Static regression guards for Gateway-side Sleep Ambient integration."""
from pathlib import Path

root = Path(__file__).resolve().parent
main = (root / "companion_gateway.py").read_text(encoding="utf-8")
required = [
    "AMBIENT_PLAYBACKS",
    "_ambient_stream_task",
    "_suspend_ambient_for_turn",
    "_ambient_reply_for_intent",
    "parse_ambient_intent(transcript, active=ambient_was_active)",
    "HOMEAI_AMBIENT_DEFAULT_VOLUME_PERCENT",
    "AMBIENT_SEGMENT_SEC",
    "[FOLLOWUP] not armed while ambient plays",
    "ambient timer already expired",
    "reminder resume skipped",
]
missing = [token for token in required if token not in main]
if missing:
    raise SystemExit(f"FAIL missing ambient integration guards: {missing}")
print("PASS Gateway ambient integration guards present")
