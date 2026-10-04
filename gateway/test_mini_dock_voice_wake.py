#!/usr/bin/env python3
"""A5.0.6 Mini dock wake probe isolation regression."""

import asyncio
import companion_gateway as g


class DummyWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


def make_mini():
    ws = DummyWS()
    s = g.ClientSession(ws=ws)
    g._apply_device_hello(s, {
        "type": "device.hello",
        "device_id": g.HOMEAI_MINI_DEVICE_ID,
        "device_role": "companion",
    })
    return s, ws


async def run_case(transcript: str, accepted: bool) -> None:
    s, ws = make_mini()
    s.audio.extend(b"NORMAL-PTT-MUST-STAY")
    s.context["sentinel"] = "keep"
    old_transcribe = g.transcribe_audio
    old_chat = g.openclaw_chat
    chat_calls = []

    async def fake_transcribe(_wav):
        return transcript, "fake"

    async def forbidden_chat(*args, **kwargs):
        chat_calls.append((args, kwargs))
        raise AssertionError("wake probe must never call OpenClaw")

    try:
        g.transcribe_audio = fake_transcribe
        g.openclaw_chat = forbidden_chat
        pcm = b"\x00\x00" * int(g.MIC_RATE * 0.5)
        await g.process_mini_wake_probe(s, pcm)
    finally:
        g.transcribe_audio = old_transcribe
        g.openclaw_chat = old_chat

    assert not chat_calls
    assert bytes(s.audio) == b"NORMAL-PTT-MUST-STAY"
    assert s.context == {"sentinel": "keep"}
    assert s.openclaw_user == g.OPENCLAW_MINI_USER
    assert ws.sent, "wake verdict missing"
    payload = ws.sent[-1]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["type"] == ("wake.accepted" if accepted else "wake.rejected")


async def main():
    assert g._is_mini_wake_phrase("逐光同学")
    assert g._is_mini_wake_phrase("逐光同学。")
    assert not g._is_mini_wake_phrase("逐光")
    assert not g._is_mini_wake_phrase("你好逐光")
    assert not g._is_mini_wake_phrase("逐光同学你好")
    await run_case("逐光同学", True)
    await run_case("逐光", False)
    print("PASS: Mini dock wake probe is strict and session-isolated")


if __name__ == "__main__":
    asyncio.run(main())
