#!/usr/bin/env python3
"""KitchenTerminal microphone/Q&A UI regression guard (R12)."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import companion_gateway as g

html = g._kitchen_html().decode("utf-8")
required = [
    g.KITCHEN_UI_VERSION,
    g.KITCHEN_PROTOCOL,
    "navigator.mediaDevices",
    "getUserMedia",
    "function qaStopTracks",
    "🎙 问逐光",
    "kitchen.qa.start",
    "qa_ptt:true",
    "加载今日菜单",
    "font-size:28px",
]
for token in required:
    assert token in html, token

for removed in [
    "micTestBtn",
    "开始 3 秒测试",
    "测试麦克风",
    "function stopTracks(",
    ".mic-grid{",
    "audio_test",
]:
    assert removed not in html, removed

# Every literal $('id') reference must point to an element that exists in the HTML.
ids = set(re.findall(r'id="([^"]+)"', html))
refs = set(re.findall(r"\$\('([^']+)'\)", html))
dynamic_ids = {"stepTimerHost", "currentTimerTime"}
missing = sorted(refs - ids - dynamic_ids)
assert not missing, f"missing DOM ids: {missing}"

# Parse the embedded JavaScript with Node when available.
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
assert len(scripts) == 1
node = shutil.which("node")
if node:
    with tempfile.TemporaryDirectory() as td:
        js = Path(td) / "kitchen.js"
        js.write_text(scripts[0], encoding="utf-8")
        subprocess.run([node, "--check", str(js)], check=True)

print("KitchenTerminal R12 microphone/Q&A UI regression: PASS")
