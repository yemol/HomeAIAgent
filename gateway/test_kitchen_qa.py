#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import io
import json
import wave

import companion_gateway as g


class DummyWS:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, payload: object) -> None:
        self.sent.append(payload)


def make_wav(seconds: float = 1.0) -> bytes:
    frames = max(1, int(16000 * seconds))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * frames)
    return buf.getvalue()


async def main_async() -> None:
    old_asr = g.transcribe_audio
    old_chat = g.openclaw_kitchen_chat
    old_speak = g._kitchen_speak

    async def fake_asr(data: bytes):
        assert data[:4] == b"RIFF"
        return "这一步为什么要小火？", "fake-asr"

    async def fake_chat(text: str) -> str:
        assert "小火" in text
        return "小火可以让内部继续受热，同时避免外层过快变干或焦掉。"

    async def fake_speak(text: str, *, kind: str = "assistant", source_id: str = ""):
        return {
            "event_id": "ka-test",
            "text": text,
            "kind": kind,
            "source_id": source_id,
            "url": "/kitchen/audio?id=ka-test",
            "expires_at": 9999999999,
        }

    g.transcribe_audio = fake_asr
    g.openclaw_kitchen_chat = fake_chat
    g._kitchen_speak = fake_speak
    try:
        g._kitchen_qa_set("idle", reset=True)
        ws = DummyWS()
        session = g.KitchenSession(ws=ws, device_id="KitchenTerminal-iPadMini-test")
        session.qa_request_id = "kq-test"
        session.qa_audio.extend(make_wav())
        await g._process_kitchen_qa(session)
        state = g._kitchen_qa_public()
        assert state["status"] == "done", state
        assert state["transcript"] == "这一步为什么要小火？", state
        assert "小火" in state["answer"], state
        assert state["audio_event_id"] == "ka-test", state
        decoded = [json.loads(x) for x in ws.sent if isinstance(x, str)]
        kinds = [x.get("type") for x in decoded]
        assert "kitchen.qa.transcript" in kinds, kinds
        assert "kitchen.qa.result" in kinds, kinds

        html = g._kitchen_html().decode("utf-8")
        assert "KitchenTerminal A3.0b" in html
        assert "homeai-kitchen/1.9" in html
        assert "🎙 问逐光" in html
        assert "kitchen.qa.start" in html
        assert "qa_ptt:true" in html
        assert "qaWav" in html
        assert "16000" in html
    finally:
        g.transcribe_audio = old_asr
        g.openclaw_kitchen_chat = old_chat
        g._kitchen_speak = old_speak
        g._kitchen_qa_set("idle", reset=True)

    print("KitchenTerminal A3.0b Q&A PTT self-test: PASS")


if __name__ == "__main__":
    asyncio.run(main_async())
