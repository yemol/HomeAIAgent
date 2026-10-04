#!/usr/bin/env python3
import asyncio
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace

_ASSET_TMP = tempfile.TemporaryDirectory()
os.environ["HOMEAI_AMBIENT_ASSET_DIR"] = _ASSET_TMP.name
_asset = Path(_ASSET_TMP.name) / "rain.pcm"
with _asset.open("wb") as _f:
    for _i in range(16000 * 4):
        _f.write(struct.pack("<h", ((_i % 200) - 100) * 80))

import companion_gateway as cg


async def main():
    loop = asyncio.get_running_loop()
    actions = []
    speaker = SimpleNamespace(
        device_id="speaker-mini-dock-01",
        parent_device_id="homeai-mini-bedroom-01",
        playback_sequence_active=False,
        playback_total_segments=0,
        playback_completed_segments=0,
        playback_done_event=asyncio.Event(),
        playback_error_event=asyncio.Event(),
        playback_slot_ready_event=asyncio.Event(),
    )
    playback = cg.AmbientPlayback(
        source_device_id="homeai-mini-bedroom-01",
        speaker_device_id=speaker.device_id,
        parent_device_id=speaker.parent_device_id,
        sound_id="rain",
        ambient_volume=20,
        normal_volume=40,
        end_monotonic=loop.time() + 30.0,
        started_monotonic=loop.time(),
    )

    orig_resolve = cg._ambient_resolve_speaker
    orig_send = cg._send_pcm_segment
    orig_wait = cg._wait_tts_event
    orig_set_volume = cg._speaker_set_volume
    orig_prefill = cg.AMBIENT_PREFILL_SEGMENTS

    async def fake_resolve(_playback, current=None):
        return speaker

    async def fake_send(_session, pcm, sample_rate, *, index, total, **kwargs):
        actions.append(("send", index, len(pcm)))

    slot_waits = 0
    async def fake_wait(_session, wanted, timeout):
        nonlocal slot_waits
        actions.append(("wait", wanted, 0))
        if wanted == "slot":
            slot_waits += 1
            speaker.playback_slot_ready_event.set()
            # First steady-state slot release happens only after both initial
            # segments must already have been queued. Stop there so the test is
            # fast and deterministic.
            if slot_waits == 1:
                playback.stop_event.set()
        elif wanted == "done":
            speaker.playback_done_event.set()

    async def fake_set_volume(_speaker, _level):
        return True

    try:
        cg._ambient_resolve_speaker = fake_resolve
        cg._send_pcm_segment = fake_send
        cg._wait_tts_event = fake_wait
        cg._speaker_set_volume = fake_set_volume
        cg.AMBIENT_PREFILL_SEGMENTS = 2
        await cg._ambient_stream_task(playback)
    finally:
        cg._ambient_resolve_speaker = orig_resolve
        cg._send_pcm_segment = orig_send
        cg._wait_tts_event = orig_wait
        cg._speaker_set_volume = orig_set_volume
        cg.AMBIENT_PREFILL_SEGMENTS = orig_prefill

    first_wait = next(i for i, item in enumerate(actions) if item[0] == "wait")
    sends_before_wait = [item for item in actions[:first_wait] if item[0] == "send"]
    if len(sends_before_wait) != 2:
        raise SystemExit(f"FAIL: expected 2 primed segments before first slot wait: {actions}")
    if [item[1] for item in sends_before_wait] != [1, 2]:
        raise SystemExit(f"FAIL: wrong prime order: {actions}")
    print("PASS runtime: two ambient segments prime before first slot wait")


if __name__ == "__main__":
    asyncio.run(main())
