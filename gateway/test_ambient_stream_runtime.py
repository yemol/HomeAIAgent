#!/usr/bin/env python3
import asyncio
import os
from pathlib import Path
import struct
import tempfile

_ASSET_TMP = tempfile.TemporaryDirectory()
os.environ["HOMEAI_AMBIENT_ASSET_DIR"] = _ASSET_TMP.name
_asset = Path(_ASSET_TMP.name) / "rain.pcm"
with _asset.open("wb") as _f:
    for _i in range(16000 * 4):
        _f.write(struct.pack("<h", ((_i % 200) - 100) * 80))

import companion_gateway as gw
from core.session import ClientSession


class FakeWS:
    async def send(self, _payload):
        return None


async def main():
    source_id = "ambient-test-source"
    speaker = ClientSession(ws=FakeWS())
    speaker.device_role = "speaker"
    speaker.device_id = "ambient-test-speaker"
    speaker.parent_device_id = source_id
    speaker.hello_received = True
    speaker.capabilities = {"volume_control": True, "volume_percent": 20}

    loop = asyncio.get_running_loop()
    playback = gw.AmbientPlayback(
        source_device_id=source_id,
        speaker_device_id=speaker.device_id,
        parent_device_id=source_id,
        sound_id="rain",
        ambient_volume=20,
        normal_volume=40,
        end_monotonic=loop.time() + 3600,
        started_monotonic=loop.time(),
    )
    gw.AMBIENT_PLAYBACKS[source_id] = playback

    sent = []
    restored = []
    original_resolve = gw._ambient_resolve_speaker
    original_send = gw._send_pcm_segment
    original_wait = gw._wait_tts_event
    original_set = gw._speaker_set_volume

    async def fake_resolve(_playback, current=None):
        return speaker

    async def fake_send(_session, pcm, sample_rate, *, index, total, **_kwargs):
        sent.append((index, total, len(pcm), sample_rate))
        if total == gw.AMBIENT_STREAM_TOTAL_SENTINEL and len(sent) >= 2:
            playback.stop_event.set()
            playback.stop_reason = "test"

    async def fake_wait(_session, _wanted, _timeout):
        return None

    async def fake_set(_session, level):
        restored.append(level)
        return level

    gw._ambient_resolve_speaker = fake_resolve
    gw._send_pcm_segment = fake_send
    gw._wait_tts_event = fake_wait
    gw._speaker_set_volume = fake_set
    try:
        await gw._ambient_stream_task(playback)
    finally:
        gw._ambient_resolve_speaker = original_resolve
        gw._send_pcm_segment = original_send
        gw._wait_tts_event = original_wait
        gw._speaker_set_volume = original_set
        gw.AMBIENT_PLAYBACKS.pop(source_id, None)

    assert len(sent) >= 3, sent
    assert sent[0][1] == gw.AMBIENT_STREAM_TOTAL_SENTINEL
    assert sent[0][2] == int(gw.AMBIENT_SAMPLE_RATE * gw.AMBIENT_SEGMENT_SEC) * 2
    final = sent[-1]
    assert final[0] == final[1], final
    assert restored and restored[-1] == 40, restored
    assert playback.done_event.is_set()
    print("PASS ambient streaming finalization + volume restore")


asyncio.run(main())
