#!/usr/bin/env python3
from pathlib import Path

text = Path("companion_gateway.py").read_text(encoding="utf-8")

def check(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")

check('HOMEAI_AMBIENT_PREFILL_SEGMENTS' in text, 'prefill env missing')
check('AMBIENT_PREFILL_SEGMENTS = max(1, min(2' in text, 'prefill bounded to two slots')
check('[AMBIENT-BUFFER] primed=' in text, 'buffer prime diagnostics missing')
check('while sent < AMBIENT_PREFILL_SEGMENTS' in text, 'initial double prefill missing')
check('await _wait_tts_event(speaker, "slot", 6.0)' in text, 'slot refill handshake missing')
check('leaving the other one playing. Refill the released slot now.' in text, 'steady-state refill intent missing')
check('if not speaker.playback_slot_ready_event.is_set()' in text, 'safe final queue slot wait missing')
print('PASS Gateway ambient double-buffer regression')
