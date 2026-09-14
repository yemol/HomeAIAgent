#!/usr/bin/env python3
from pathlib import Path
import ast

SRC = Path(__file__).with_name("companion_gateway.py")
text = SRC.read_text(encoding="utf-8")
ast.parse(text)
assert 'NETWORK_SPEAKER_TTS_SEGMENT_MAX_BYTES' in text
assert 'str(256 * 1024)' in text
assert 'NETWORK_SPEAKER_TTS_CHUNK_BYTES' in text
assert '"4096"' in text
assert 'role={session.device_role}' in text
assert 'max_bytes=_tts_segment_max_bytes_for_session(current)' in text
assert '_send_network_pcm_with_recovery' in text
assert 'AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS' in text
print('NetworkSpeaker constrained transport profile: PASS')
