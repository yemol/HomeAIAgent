#!/usr/bin/env python3
import importlib.util
import math
import struct
import sys
from pathlib import Path

base = Path(__file__).resolve().parent
p = base / "companion_gateway.py"
name = "homeai_long_tts_test"
spec = importlib.util.spec_from_file_location(name, p)
m = importlib.util.module_from_spec(spec)
sys.modules[name] = m
assert spec.loader
spec.loader.exec_module(m)

rate = 16000
seconds = 67
samples = []
for i in range(rate * seconds):
    # Quiet 200 ms pause every 10 seconds to give the splitter natural cut points.
    phase_sec = i / rate
    if int(phase_sec * 5) % 50 == 0:
        v = 0
    else:
        v = int(5000 * math.sin(2 * math.pi * 220 * phase_sec))
    samples.append(v)

pcm = struct.pack("<" + "h" * len(samples), *samples)
segments = m.split_pcm_for_device(pcm, rate)

assert len(pcm) > 1536 * 1024
assert len(segments) >= 3
assert sum(map(len, segments)) == len(pcm)
assert all(len(seg) <= m.DEVICE_TTS_SEGMENT_MAX_BYTES for seg in segments)
assert all(len(seg) % 2 == 0 for seg in segments)

print("Long-TTS segmentation self-test: PASS")
print(f"total={len(pcm)} bytes segments={[len(s) for s in segments]}")
