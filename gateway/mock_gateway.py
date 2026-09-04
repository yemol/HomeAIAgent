#!/usr/bin/env python3
"""P0 local WebSocket mock.

This does not call OpenClaw, ASR or TTS yet. It only proves the transport and
LCD state machine before external services are introduced.
"""

import asyncio
import json

import websockets

HOST = "0.0.0.0"
PORT = 8765


async def send_state(ws, state: str):
    await ws.send(json.dumps({"type": "assistant.state", "state": state}, ensure_ascii=False))


async def handle(ws):
    print("client connected")
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            print("<-", msg)
            kind = msg.get("type")

            if kind == "device.hello":
                await send_state(ws, "idle")
            elif kind == "ptt.start":
                await send_state(ws, "listening")
            elif kind == "ptt.stop":
                await send_state(ws, "thinking")
                await asyncio.sleep(1.0)
                await send_state(ws, "speaking")
                await asyncio.sleep(2.5)
                await send_state(ws, "idle")
    finally:
        print("client disconnected")


async def main():
    async with websockets.serve(handle, HOST, PORT):
        print(f"mock gateway listening on ws://{HOST}:{PORT}/companion")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
