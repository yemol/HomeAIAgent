#!/usr/bin/env python3
import asyncio
import importlib.util
import sys
from pathlib import Path

base = Path(__file__).resolve().parent
gateway = base / "companion_gateway.py"
name = "homeai_gateway_asr2_live_test"
spec = importlib.util.spec_from_file_location(name, gateway)
m = importlib.util.module_from_spec(spec)
sys.modules[name] = m
assert spec.loader
spec.loader.exec_module(m)

wav_path = m.HOMEAI_DEBUG_DIR / "latest_input.wav"
if not wav_path.exists():
    raise SystemExit(
        f"latest_input.wav 不存在。先让设备录一次音；默认路径：{m.HOMEAI_DEBUG_DIR / 'latest_input.wav'}"
    )

async def main():
    print(f"[TEST] ASR endpoint={m.VOLCENGINE_ASR_ENDPOINT}")
    print(f"[TEST] ASR resource={m.VOLCENGINE_ASR_RESOURCE_ID}")
    text = await m.volcengine_transcribe(wav_path.read_bytes())
    print(f"[PASS] transcript={text}")

asyncio.run(main())
