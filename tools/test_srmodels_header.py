#!/usr/bin/env python3
import struct

MODEL_NAME = "mn5q8_cn"
FILES = ["_MODEL_INFO_", "mn5q8_data", "mn5q8_index"]

def pack_ascii(value: str, width: int) -> bytes:
    raw = value.encode("ascii")
    if len(raw) > width:
        raise ValueError(value)
    return raw + (b"\x00" * (width - len(raw)))

model_count = 1
file_count = len(FILES)
header_len = 4 + model_count * (32 + 4) + file_count * (32 + 4 + 4)

header = bytearray()
header += struct.pack("<I", model_count)
header += pack_ascii(MODEL_NAME, 32)
header += struct.pack("<I", file_count)

offset = header_len
for i, filename in enumerate(FILES):
    fake_size = (i + 1) * 10
    header += pack_ascii(filename, 32)
    header += struct.pack("<I", offset)
    header += struct.pack("<I", fake_size)
    offset += fake_size

assert len(header) == header_len, (len(header), header_len)
assert header_len == 160, header_len

print("[PASS] srmodels header length = 160 bytes")
print("[PASS] ASCII fields are NUL padded with real 0x00 bytes")
