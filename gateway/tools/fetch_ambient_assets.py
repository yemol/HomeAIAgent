#!/usr/bin/env python3
"""Install real sleep-audio assets from Wikimedia Commons.

The Gateway runtime is intentionally offline after installation. This tool is
run once (or when the library changes): it resolves each Commons original,
downloads it, converts it to 16 kHz / PCM16 / mono, and stores the result under
~/.local/share/HomeAIAgent/ambient_assets by default.

Only public-domain / CC0 sources are in the production manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

SAMPLE_RATE = 16000
DEFAULT_DIR = Path(os.getenv(
    "HOMEAI_AMBIENT_ASSET_DIR",
    str(Path.home() / ".local" / "share" / "HomeAIAgent" / "ambient_assets"),
)).expanduser()
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "HomeAIAgent-AmbientAssetInstaller/1.0 (local personal assistant)"

# Keep this list in the same order as ambient_sleep.SOUNDS.
LIBRARY = (
    {
        "id": "rain",
        "label": "轻柔雨声",
        "title": "Rain (1).ogg",
        "page": "https://commons.wikimedia.org/wiki/File:Rain_(1).ogg",
        "author": "ezwa / PDSounds",
        "license": "Public Domain",
    },
    {
        "id": "rain_thunder",
        "label": "雨夜雷声",
        "title": "Rain and thunder (1).ogg",
        "page": "https://commons.wikimedia.org/wiki/File:Rain_and_thunder_(1).ogg",
        "author": "ezwa / PDSounds",
        "license": "Public Domain",
    },
    {
        "id": "ocean",
        "label": "舒缓海浪",
        "title": "Ocean Waves on a Tropical Beach.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:Ocean_Waves_on_a_Tropical_Beach.ogg",
        "author": "Jarrod Stanley / J.D. Savanyu",
        "license": "CC0 1.0",
    },
    {
        "id": "stream",
        "label": "山间水流",
        "title": "Sanna river rapids.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:Sanna_river_rapids.ogg",
        "author": "Jarrod Stanley / J.D. Savanyu",
        "license": "CC0 1.0",
    },
    {
        "id": "fireplace",
        "label": "壁炉柴火",
        "title": "Dry grass burning in open fireplace.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:Dry_grass_burning_in_open_fireplace.ogg",
        "author": "ezwa / PDSounds",
        "license": "Public Domain",
    },
    {
        "id": "rainy_jazz",
        "label": "雨夜爵士",
        "title": "Rainy Mood and Jazz Music by Tripacer.oga",
        "page": "https://commons.wikimedia.org/wiki/File:Rainy_Mood_and_Jazz_Music_by_Tripacer.oga",
        "author": "Tripacer",
        "license": "Public Domain",
    },
    {
        "id": "foggy_ambient",
        "label": "雾林氛围音乐",
        "title": "John Bartmann - foggy-trees-master.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:John_Bartmann_-_foggy-trees-master.ogg",
        "author": "John Bartmann",
        "license": "CC0 1.0",
    },
    {
        "id": "mountain_ambient",
        "label": "山间氛围音乐",
        "title": "John Bartmann - back-to-the-mountain-master.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:John_Bartmann_-_back-to-the-mountain-master.ogg",
        "author": "John Bartmann",
        "license": "CC0 1.0",
    },
    {
        "id": "cendence_ambient",
        "label": "深层氛围音乐",
        "title": "John Bartmann - cendence-master.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:John_Bartmann_-_cendence-master.ogg",
        "author": "John Bartmann",
        "license": "CC0 1.0",
    },
)


def _request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def _resolve_original(title: str) -> str:
    query = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "titles": f"File:{title}",
    })
    data = _request_json(f"{COMMONS_API}?{query}")
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        raise RuntimeError(f"Commons file not found: {title}")
    info = (pages[0].get("imageinfo") or [{}])[0]
    url = str(info.get("url") or "")
    if not url.startswith("https://upload.wikimedia.org/"):
        raise RuntimeError(f"Unexpected Commons original URL for {title}: {url!r}")
    return url


def _download(url: str, dst: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp, dst.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _convert(ffmpeg: str, src: Path, dst: Path) -> None:
    # Normalize gently so one track is not wildly louder than another. The
    # user's sleep volume is still controlled independently by Dock volume.
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-af", "loudnorm=I=-24:LRA=11:TP=-3",
        "-f", "s16le", "-acodec", "pcm_s16le", str(dst),
    ]
    subprocess.run(cmd, check=True)
    if dst.stat().st_size < SAMPLE_RATE * 2 * 3:
        raise RuntimeError(f"Converted asset is unexpectedly short: {dst}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--only", action="append", default=[], metavar="SOUND_ID")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-source", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for idx, item in enumerate(LIBRARY, 1):
            print(f"{idx:2d}. {item['id']:<18} {item['label']}  [{item['license']}] {item['author']}")
        return 0

    selected = list(LIBRARY)
    if args.only:
        wanted = set(args.only)
        known = {item["id"] for item in LIBRARY}
        unknown = sorted(wanted - known)
        if unknown:
            parser.error(f"unknown sound id(s): {', '.join(unknown)}")
        selected = [item for item in LIBRARY if item["id"] in wanted]

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ERROR: ffmpeg not found. On macOS: brew install ffmpeg", file=sys.stderr)
        return 2

    asset_dir = args.asset_dir.expanduser().resolve()
    asset_dir.mkdir(parents=True, exist_ok=True)
    records = []
    failures = []

    print(f"[AMBIENT-ASSET] destination={asset_dir}")
    with tempfile.TemporaryDirectory(prefix="homeai-ambient-") as tmp_s:
        tmp = Path(tmp_s)
        for idx, item in enumerate(selected, 1):
            sound_id = item["id"]
            dst = asset_dir / f"{sound_id}.pcm"
            print(f"[{idx}/{len(selected)}] {item['label']} ({sound_id})")
            try:
                if dst.exists() and not args.force:
                    print(f"  reuse {dst.name} ({dst.stat().st_size / 1024 / 1024:.1f} MiB)")
                    original_url = ""
                else:
                    original_url = _resolve_original(item["title"])
                    suffix = Path(urllib.parse.urlparse(original_url).path).suffix or ".media"
                    src = tmp / f"{sound_id}{suffix}"
                    print("  download Commons original")
                    _download(original_url, src)
                    print("  convert -> PCM16 mono 16kHz")
                    work = dst.with_suffix(".pcm.tmp")
                    try:
                        _convert(ffmpeg, src, work)
                        os.replace(work, dst)
                    finally:
                        if work.exists():
                            work.unlink()
                    if args.keep_source:
                        keep = asset_dir / f"{sound_id}{suffix}"
                        shutil.copy2(src, keep)
                seconds = dst.stat().st_size / (SAMPLE_RATE * 2)
                records.append({
                    **item,
                    "installed_file": dst.name,
                    "pcm_sha256": _sha256(dst),
                    "pcm_bytes": dst.stat().st_size,
                    "duration_sec": round(seconds, 3),
                    "sample_rate": SAMPLE_RATE,
                    "channels": 1,
                    "sample_format": "s16le",
                    "resolved_original_url": original_url,
                })
                print(f"  OK {seconds/60:.1f} min, {dst.stat().st_size/1024/1024:.1f} MiB")
            except Exception as exc:
                failures.append((sound_id, exc))
                print(f"  FAIL {type(exc).__name__}: {exc}", file=sys.stderr)

    manifest = {
        "schema": "homeai-ambient-assets/1",
        "sample_rate": SAMPLE_RATE,
        "library": records,
    }
    (asset_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if failures:
        print("\nSome assets failed:", file=sys.stderr)
        for sound_id, exc in failures:
            print(f"  {sound_id}: {exc}", file=sys.stderr)
        return 1
    print(f"\n[AMBIENT-ASSET] installed={len(records)} manifest={asset_dir/'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
