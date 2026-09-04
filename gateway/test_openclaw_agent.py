#!/usr/bin/env python3
import asyncio
import importlib.util
import sys
import traceback
from pathlib import Path

base = Path(__file__).resolve().parent
p = base / "companion_gateway.py"
name = "homeai_agent_rebase_test"
spec = importlib.util.spec_from_file_location(name, p)
m = importlib.util.module_from_spec(spec)
sys.modules[name] = m
assert spec.loader
spec.loader.exec_module(m)

async def main():
    print("=== HomeAIAgent OpenClaw direct test ===")
    print(f"[CFG] {m.OPENCLAW_BASE_URL}/v1/chat/completions")
    print(f"[CFG] model={m.OPENCLAW_MODEL} user={m.OPENCLAW_USER} token_present={bool(m.OPENCLAW_TOKEN)}")
    try:
        ans = await m.openclaw_chat("介绍一下你自己。", {"current":{}, "previous":{}, "next":{}})
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise SystemExit(2)
    print(f"[PASS] {ans}")

asyncio.run(main())
