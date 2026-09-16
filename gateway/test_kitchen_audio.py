#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import struct

import companion_gateway as cg


async def fake_synth(text: str):
    rate = 16000
    # 80 ms of quiet PCM16, enough to validate WAV packaging/cache without provider access.
    samples = [0] * int(rate * 0.08)
    pcm = b"".join(struct.pack("<h", x) for x in samples)
    return pcm, rate, "fake"


def test_media_range_response() -> None:
    payload = bytes(range(100))
    full = cg._kitchen_audio_http_response(payload, "")
    assert full.status_code == 200
    assert full.body == payload
    assert full.headers["Accept-Ranges"] == "bytes"

    part = cg._kitchen_audio_http_response(payload, "bytes=10-19")
    assert part.status_code == 206
    assert part.body == payload[10:20]
    assert part.headers["Content-Range"] == "bytes 10-19/100"
    assert part.headers["Content-Length"] == "10"

    suffix = cg._kitchen_audio_http_response(payload, "bytes=-8")
    assert suffix.status_code == 206
    assert suffix.body == payload[-8:]
    assert suffix.headers["Content-Range"] == "bytes 92-99/100"

    invalid = cg._kitchen_audio_http_response(payload, "bytes=100-120")
    assert invalid.status_code == 416
    assert invalid.headers["Content-Range"] == "bytes */100"


async def main_async() -> None:
    old = cg.synthesize_speech
    cg.synthesize_speech = fake_synth
    try:
        event = await cg._kitchen_speak("厨房语音测试", kind="test")
        assert event and event["event_id"]
        assert event["url"].startswith("/kitchen/audio?id=")
        assert event["event_id"] in cg.KITCHEN_AUDIO_CACHE
        assert cg.KITCHEN_AUDIO_CACHE[event["event_id"]][:4] == b"RIFF"
        assert cg._is_kitchen_related_utterance("逐光，显示今天的菜谱")
        cg.KITCHEN_CURRENT_MENU = {"recipes": [{"name": "葱姜盐水河虾"}]}
        cg.KITCHEN_CURRENT_STATE = {"screen": "recipe", "dish": "葱姜盐水河虾", "step": 3}
        assert cg._is_kitchen_related_utterance("这个还要煮多久")
        assert cg._is_kitchen_related_utterance("河虾怎么做")
        assert not cg._is_kitchen_related_utterance("明天天气怎么样")
    finally:
        cg.synthesize_speech = old
    print("KitchenTerminal R16 HTML-media/AirPlay audio self-test: PASS")


if __name__ == "__main__":
    test_media_range_response()
    asyncio.run(main_async())
