#!/usr/bin/env python3
from pathlib import Path
import companion_gateway as g

assert g.HOMEAI_DEBUG_DIR.parent == g.HOMEAI_DATA_DIR
source = Path(__file__).with_name("companion_gateway.py").read_text(encoding="utf-8")
for forbidden in [
    'BASE_DIR / "latest_input.wav"',
    'BASE_DIR / "latest_input_kitchen.wav"',
    'BASE_DIR / "latest_transcript.txt"',
    'BASE_DIR / "latest_answer.txt"',
    'BASE_DIR / "latest_tts.wav"',
]:
    assert forbidden not in source, forbidden
print("Runtime diagnostics outside source tree: PASS")
