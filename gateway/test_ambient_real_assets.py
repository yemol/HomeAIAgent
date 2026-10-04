#!/usr/bin/env python3
from pathlib import Path
import re

import ambient_sleep as a

root = Path(__file__).resolve().parent
fetch = (root / "tools" / "fetch_ambient_assets.py").read_text(encoding="utf-8")
gateway = (root / "companion_gateway.py").read_text(encoding="utf-8")
env = (root / "gateway.env.example").read_text(encoding="utf-8")

ids = [s.sound_id for s in a.SOUNDS]
manifest_ids = re.findall(r'^\s*"id":\s*"([^"]+)"', fetch, flags=re.M)
if ids != manifest_ids:
    raise SystemExit(f"FAIL library/installer order mismatch: {ids} != {manifest_ids}")
if len(ids) != 9:
    raise SystemExit(f"FAIL expected 9 real-audio choices, got {len(ids)}")
if "class AmbientSynth" in (root / "ambient_sleep.py").read_text(encoding="utf-8"):
    raise SystemExit("FAIL procedural AmbientSynth still exists")
if 'os.getenv("HOMEAI_AMBIENT_DEFAULT_VOLUME_PERCENT", "5")' not in gateway:
    raise SystemExit("FAIL runtime default ambient volume is not 5%")
if "HOMEAI_AMBIENT_DEFAULT_VOLUME_PERCENT=5" not in env:
    raise SystemExit("FAIL env example default ambient volume is not 5%")
for token in ("Public Domain", "CC0 1.0", "Wikimedia Commons"):
    if token not in fetch:
        raise SystemExit(f"FAIL installer provenance missing {token}")
print("PASS real-audio library / installer / 5% default regression")
