#!/usr/bin/env python3
import os
from pathlib import Path
import struct
import tempfile

from ambient_sleep import (
    AMBIENT_SAMPLE_RATE,
    AmbientAssetLoop,
    SOUNDS,
    format_duration_zh,
    parse_ambient_intent,
    parse_duration_sec,
    sound_catalog_numbered_text,
    sound_id_for_number,
)


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")


cases = [
    ("播放雨声30分钟", False, "start", "rain", 1800),
    ("播放雨夜雷声30分钟", False, "start", "rain_thunder", 1800),
    ("播放海浪声一个小时", False, "start", "ocean", 3600),
    ("播放山间水流半小时", False, "start", "stream", 1800),
    ("播放雨夜爵士45分钟", False, "start", "rainy_jazz", 2700),
    ("播放深层氛围音乐半小时", False, "start", "cendence_ambient", 1800),
    ("换成壁炉声", True, "switch", "fireplace", None),
    ("停止雨声", True, "stop", "", None),
    ("不要播了", True, "stop", "", None),
    ("再放30分钟", True, "extend", "", 1800),
    ("声音小一点", True, "volume", "", None),
    ("雨声音量调到5%", True, "volume", "", None),
    ("还剩多久", True, "status", "", None),
    ("有哪些助眠声音", False, "list", "", None),
    ("打开助眠声音菜单", False, "list", "", None),
    ("播放第3个30分钟", False, "start", "ocean", 1800),
    ("播放第九种半小时", False, "start", "cendence_ambient", 1800),
    ("换成第5个", True, "switch", "fireplace", None),
    ("换一个", True, "next", "", None),
    ("播放第10个30分钟", False, "invalid_selection", "", 1800),
]
for text, active, op, sound, duration in cases:
    intent = parse_ambient_intent(text, active=active)
    check(intent is not None, f"no intent for {text}")
    check(intent.operation == op, f"{text}: op={intent.operation} expected={op}")
    if sound:
        check(intent.sound_id == sound, f"{text}: sound={intent.sound_id} expected={sound}")
    if duration is not None:
        check(intent.duration_sec == duration, f"{text}: duration={intent.duration_sec} expected={duration}")

check(parse_duration_sec("两个半小时") == 9000, "two and a half hours")
check(parse_duration_sec("90分钟") == 5400, "90 minutes")
check(format_duration_zh(5400) == "1小时30分钟", "duration formatting")
check(sound_id_for_number(1) == "rain", "sound #1")
check(sound_id_for_number(9) == "cendence_ambient", "sound #9")
catalog = sound_catalog_numbered_text()
check("1轻柔雨声" in catalog and "9深层氛围音乐" in catalog, "numbered catalog")
check(len(SOUNDS) == 9, "production sound count")

# Exercise the real-asset loop path with a deterministic PCM fixture. No synth
# class should exist in the production runtime anymore.
with tempfile.TemporaryDirectory() as td:
    asset = Path(td) / "rain.pcm"
    frames = AMBIENT_SAMPLE_RATE * 3
    with asset.open("wb") as f:
        for i in range(frames):
            sample = int(10000 * ((i % 101) / 100.0 - 0.5))
            f.write(struct.pack("<h", sample))
    loop = AmbientAssetLoop("rain", asset_dir=td, crossfade_sec=0.25)
    pcm = loop.render(0.2)
    check(len(pcm) == int(AMBIENT_SAMPLE_RATE * 0.2) * 2, "asset PCM length")
    check(any(pcm), "asset PCM is all zero")

print(f"PASS ambient parser + {len(SOUNDS)} real-audio choices + asset loop")
