#!/usr/bin/env python3
import gzip
import importlib.util
import json
import struct
import sys
from pathlib import Path

p = Path(__file__).with_name("companion_gateway.py")
name = "homeai_gateway_protocol_test"
spec = importlib.util.spec_from_file_location(name, p)
m = importlib.util.module_from_spec(spec)
sys.modules[name] = m
assert spec.loader
spec.loader.exec_module(m)

# ---------- ASR full request ----------
req = {"audio":{"format":"pcm","rate":16000},"request":{"model_name":"bigmodel"}}
pkt = m._volc_asr_full_request_packet(req)
assert pkt[:4] == bytes((0x11,0x10,0x11,0x00))
size = struct.unpack_from(">I", pkt, 4)[0]
payload = gzip.decompress(pkt[8:8+size])
assert json.loads(payload.decode("utf-8"))["audio"]["rate"] == 16000

# ---------- ASR normal audio ----------
audio = b"\x01\x00" * 320
pkt = m._volc_asr_audio_packet(audio, last=False)
assert pkt[:4] == bytes((0x11,0x20,0x01,0x00))
size = struct.unpack_from(">I", pkt, 4)[0]
assert gzip.decompress(pkt[8:8+size]) == audio

# ---------- ASR last audio ----------
pkt = m._volc_asr_audio_packet(audio, last=True)
assert pkt[:4] == bytes((0x11,0x22,0x01,0x00))

# Synthetic full-server final frame, gzip JSON, sequence -3.
body = {
    "result": {
        "text": "你好，你是谁？",
        "utterances": [{"text":"你好，你是谁？","definite":True}]
    }
}
raw = gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8"))
server = (
    bytes((0x11,0x93,0x11,0x00))
    + struct.pack(">i", -3)
    + struct.pack(">I", len(raw))
    + raw
)
parsed = m._volc_asr_parse_frame(server)
assert parsed["is_last"] is True
assert parsed["sequence"] == -3
text, definite = m._volc_asr_extract_text(parsed["payload"])
assert text == "你好，你是谁？"
assert definite is True

# ---------- TTS framing regression ----------
pkt = m._volc_tts_send_text_packet({"user":{"uid":"t"},"req_params":{"text":"你好"}})
assert pkt[:4] == bytes((0x11,0x10,0x10,0x00))
n = struct.unpack_from(">I", pkt, 4)[0]
assert n == len(pkt) - 8

print("HomeAIAgent ASR2 + TTS V3 protocol self-test: PASS")
