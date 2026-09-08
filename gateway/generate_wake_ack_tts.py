#!/usr/bin/env python3
"""Generate HomeAIAgent's local wake acknowledgement with the production Volcengine TTS voice.

This is intentionally a ONE-TIME asset generation tool. It reads the same persistent
HomeAIAgent speech configuration as companion_gateway.py, synthesizes ``在的。`` with
the production voice, performs only conservative silence trimming + linear peak/RMS
normalization (no dynamic compression), and writes:

  ../include/wake_ack_voice_pcm.h
  ../assets/wake_ack_zaide_tts.wav

After this runs successfully, StickS3 playback is fully local and consumes no cloud
TTS requests during normal wake-word use.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import wave
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INCLUDE_PATH = ROOT / "include" / "wake_ack_voice_pcm.h"
ASSET_DIR = ROOT / "assets"
WAV_PATH = ASSET_DIR / "wake_ack_zaide_tts.wav"

EXPECTED_RESOURCE = "seed-tts-2.0"
EXPECTED_VOICE = "zh_female_vv_uranus_bigtts"
EXPECTED_RATE = 16000
DEFAULT_TEXT = "在的。"


def dbfs(value: float) -> float:
    if value <= 0:
        return float("-inf")
    return 20.0 * math.log10(value / 32767.0)


def _rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(float(v) * float(v) for v in samples) / len(samples))


def _active_window(samples: list[int], threshold: int = 500) -> tuple[int, int]:
    """Return approximate voiced region using 10 ms absolute-mean windows."""
    if not samples:
        return 0, 0
    win = 160  # 10 ms at 16 kHz
    first = None
    last = None
    for pos in range(0, len(samples), win):
        block = samples[pos:pos + win]
        if not block:
            break
        mean_abs = sum(abs(v) for v in block) / len(block)
        peak = max(abs(v) for v in block)
        if mean_abs >= threshold or peak >= threshold * 3:
            if first is None:
                first = pos
            last = min(len(samples), pos + len(block))
    if first is None or last is None:
        return 0, len(samples)
    return first, last


def process_pcm(pcm: bytes, rate: int) -> tuple[list[int], dict[str, float]]:
    if rate != EXPECTED_RATE:
        raise RuntimeError(f"wake ACK requires {EXPECTED_RATE} Hz; TTS returned {rate} Hz")
    if len(pcm) % 2:
        pcm = pcm[:-1]
    raw = array("h")
    raw.frombytes(pcm)
    if sys.byteorder != "little":
        raw.byteswap()
    samples = list(raw)
    if not samples:
        raise RuntimeError("TTS returned empty PCM")

    # Preserve natural wording. Only remove excessive provider silence while keeping
    # enough pre/post roll so the first consonant and natural tail are never clipped.
    active_start, active_end = _active_window(samples)
    pre_roll = int(rate * 0.035)   # 35 ms
    post_roll = int(rate * 0.180)  # 180 ms natural tail
    start = max(0, active_start - pre_roll)
    end = min(len(samples), active_end + post_roll)
    samples = samples[start:end]

    # Compute active speech RMS after trimming.
    a0, a1 = _active_window(samples)
    active = samples[a0:a1] if a1 > a0 else samples
    peak_before = max(abs(v) for v in samples) or 1
    rms_before = _rms(active)

    # No compression. Use a single linear gain only. Keep enough headroom for MAG6
    # and cap active RMS so the tiny speaker is not driven into the harsh region.
    target_peak = 32767.0 * (10.0 ** (-3.0 / 20.0))     # -3 dBFS
    target_rms = 32767.0 * (10.0 ** (-17.0 / 20.0))    # -17 dBFS
    gain_by_peak = target_peak / peak_before
    gain_by_rms = target_rms / rms_before if rms_before > 0 else gain_by_peak
    gain = min(gain_by_peak, gain_by_rms)
    # Never attenuate a naturally quiet clip below its original loudness unless
    # peak/RMS safety requires it; allow up to +9 dB linear gain.
    gain = min(gain, 10.0 ** (9.0 / 20.0))

    processed = [
        max(-32767, min(32767, int(round(v * gain))))
        for v in samples
    ]

    # Tiny fades only prevent edge clicks; they do not shorten or compress speech.
    fade_in = min(len(processed), int(rate * 0.006))
    fade_out = min(len(processed), int(rate * 0.012))
    for i in range(fade_in):
        processed[i] = int(round(processed[i] * (i / max(1, fade_in - 1))))
    for i in range(fade_out):
        idx = len(processed) - fade_out + i
        processed[idx] = int(round(processed[idx] * ((fade_out - 1 - i) / max(1, fade_out - 1))))

    p0, p1 = _active_window(processed)
    active_after = processed[p0:p1] if p1 > p0 else processed
    peak_after = max(abs(v) for v in processed) or 0
    rms_after = _rms(active_after)
    clipped = sum(1 for v in processed if abs(v) >= 32767)
    metrics = {
        "duration_ms": len(processed) * 1000.0 / rate,
        "peak_before_dbfs": dbfs(float(peak_before)),
        "rms_before_dbfs": dbfs(rms_before),
        "gain_db": 20.0 * math.log10(gain) if gain > 0 else float("-inf"),
        "peak_after_dbfs": dbfs(float(peak_after)),
        "rms_after_dbfs": dbfs(rms_after),
        "clipped_samples": float(clipped),
    }
    return processed, metrics


def write_wav(samples: list[int], path: Path, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array("h", samples)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())


def write_header(samples: list[int], path: Path, metrics: dict[str, float], text: str) -> None:
    lines: list[str] = []
    for pos in range(0, len(samples), 12):
        chunk = samples[pos:pos + 12]
        lines.append("  " + ", ".join(str(v) for v in chunk) + ",")
    header = f'''#pragma once

#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>

// A1R19 local wake acknowledgement generated ONCE with HomeAIAgent's production TTS.
// Text: {text}
// Resource: {EXPECTED_RESOURCE}
// Voice: {EXPECTED_VOICE}
// 16 kHz mono signed PCM16. No dynamic compression; only conservative silence trim,
// linear normalization and tiny anti-click fades. Normal wake playback is fully local.
// Generated metrics: duration={metrics['duration_ms']:.0f} ms, peak={metrics['peak_after_dbfs']:.2f} dBFS,
// active_rms={metrics['rms_after_dbfs']:.2f} dBFS, linear_gain={metrics['gain_db']:+.2f} dB,
// clipped_samples={int(metrics['clipped_samples'])}.
#define HOMEAI_WAKE_ACK_TTS_ASSET_READY 1
static constexpr uint32_t kWakeAckVoiceSampleRate = {EXPECTED_RATE};
static const int16_t kWakeAckVoicePcm[] PROGMEM = {{
{chr(10).join(lines)}
}};
static constexpr size_t kWakeAckVoiceSampleCount =
    sizeof(kWakeAckVoicePcm) / sizeof(kWakeAckVoicePcm[0]);
static constexpr uint32_t kWakeAckVoiceDurationMs = static_cast<uint32_t>(
    (static_cast<uint64_t>(kWakeAckVoiceSampleCount) * 1000ULL +
     kWakeAckVoiceSampleRate - 1ULL) /
    kWakeAckVoiceSampleRate);
'''
    path.write_text(header, encoding="utf-8")


async def generate(text: str) -> None:
    # Import only after environment has been sourced by the shell wrapper.
    import companion_gateway as cg

    if cg.VOLCENGINE_TTS_RESOURCE_ID != EXPECTED_RESOURCE:
        raise RuntimeError(
            f"unexpected TTS resource: {cg.VOLCENGINE_TTS_RESOURCE_ID!r}; expected {EXPECTED_RESOURCE!r}"
        )
    if cg.VOLCENGINE_TTS_VOICE != EXPECTED_VOICE:
        raise RuntimeError(
            f"unexpected TTS voice: {cg.VOLCENGINE_TTS_VOICE!r}; expected {EXPECTED_VOICE!r}"
        )

    print(f"[WAKE-ACK-TTS] synthesizing text={text!r}")
    print(f"[WAKE-ACK-TTS] resource={EXPECTED_RESOURCE}")
    print(f"[WAKE-ACK-TTS] voice={EXPECTED_VOICE}")
    pcm, rate = await cg.volcengine_tts_pcm(text)
    samples, metrics = process_pcm(pcm, rate)
    write_wav(samples, WAV_PATH, rate)
    write_header(samples, INCLUDE_PATH, metrics, text)

    print(f"[WAKE-ACK-TTS] generated samples={len(samples)} duration={metrics['duration_ms']:.0f}ms")
    print(
        f"[WAKE-ACK-TTS] peak={metrics['peak_after_dbfs']:.2f}dBFS "
        f"active_rms={metrics['rms_after_dbfs']:.2f}dBFS "
        f"clipped={int(metrics['clipped_samples'])}"
    )
    print(f"[WAKE-ACK-TTS] preview={WAV_PATH}")
    print(f"[WAKE-ACK-TTS] header={INCLUDE_PATH}")
    print("[READY] Local TTS wake acknowledgement asset generated.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()
    asyncio.run(generate(args.text))


if __name__ == "__main__":
    main()
