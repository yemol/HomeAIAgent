#!/usr/bin/env python3
"""HomeAIAgent P0-A4.2 gateway: 3-line Glass2 + night screen protection.

Default speech path:
  Doubao Streaming ASR 2.0 -> remote OpenClaw -> Doubao TTS 2.0.

ASR uses the optimized bidirectional streaming WebSocket endpoint:
  wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async

ASR 2.0 duration resource:
  volc.seedasr.sauc.duration

TTS remains the validated V3 unidirectional WebSocket path.

One new-console Doubao Speech API Key is used for ASR/TTS through X-Api-Key.
The StickS3 never receives speech-provider or OpenClaw secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import hashlib
import io
import json
import math
import os
import array
import struct
import time
import traceback
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
import httpx
import websockets
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# HomeAIAgent machine configuration is intentionally OUTSIDE the project tree.
# Whole-project replacement must never erase API keys, OpenClaw credentials,
# tunnel settings, or machine-specific endpoints.
PERSISTENT_CONFIG_DIR = Path(
    os.getenv(
        "HOMEAI_CONFIG_DIR",
        str(Path.home() / ".config" / "HomeAIAgent"),
    )
).expanduser()
PERSISTENT_ENV = Path(
    os.getenv(
        "HOMEAI_CONFIG_FILE",
        str(PERSISTENT_CONFIG_DIR / "gateway.env"),
    )
).expanduser()
LEGACY_ENV = BASE_DIR / ".env"


def _load_homeai_environment() -> None:
    # Process environment always wins over dotenv values.
    if PERSISTENT_ENV.exists():
        load_dotenv(PERSISTENT_ENV, override=False)
        print(f"[CONFIG] persistent={PERSISTENT_ENV}")
        return

    # One-time automatic migration from old project-local .env.
    if LEGACY_ENV.exists():
        try:
            PERSISTENT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = LEGACY_ENV.read_bytes()
            tmp = PERSISTENT_ENV.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.chmod(tmp, 0o600)
            tmp.replace(PERSISTENT_ENV)
            print(f"[CONFIG] migrated legacy .env -> {PERSISTENT_ENV}")
            load_dotenv(PERSISTENT_ENV, override=False)
            return
        except OSError as exc:
            print(
                f"[CONFIG-WARN] persistent migration failed: "
                f"{type(exc).__name__}: {exc}"
            )
            # Never block an otherwise working system; use the legacy file.
            load_dotenv(LEGACY_ENV, override=False)
            print(f"[CONFIG] using legacy={LEGACY_ENV}")
            return

    print(
        f"[CONFIG-WARN] no config found. Expected {PERSISTENT_ENV}. "
        f"Run ./setup_mac.sh"
    )


_load_homeai_environment()

HOMEAI_DATA_DIR = Path(
    os.getenv(
        "HOMEAI_DATA_DIR",
        str(Path.home() / ".local" / "share" / "HomeAIAgent"),
    )
).expanduser()

INFO_SKILL_PROTOCOL = "homeai-info/1.1"
INFO_MAX_ITEMS = 20
INFO_GAME_LIMIT = 10
INFO_FINANCE_LIMIT = 10
INFO_HEADLINE_MAX_CHARS = 36
INFO_SUMMARY_MAX_CHARS = 160
INFO_MAX_AGE_HOURS = int(os.getenv("HOMEAI_INFO_MAX_AGE_HOURS", "96"))

# A4.1.2 fixed wall-clock schedule.
# Fetch at 00:00, 01:00, then pause 02:00-08:59, resume at 09:00,
# and continue hourly through 23:00.
INFO_SCHEDULE_TIMEZONE = os.getenv("HOMEAI_INFO_TIMEZONE", "Asia/Taipei").strip()
INFO_ALLOWED_HOURS = frozenset([0, 1, *range(9, 24)])

# A4.2 screen protection. The Gateway is the wall-clock authority so the
# StickS3 does not need NTP/Internet time of its own.
DISPLAY_SLEEP_HOUR = 1
DISPLAY_SLEEP_MINUTE = 5
DISPLAY_WAKE_HOUR = 9
DISPLAY_WAKE_MINUTE = 0

INFO_FRAME_WIDTH = 128
INFO_FRAME_HEIGHT = 64

INFO_SKILL_CACHE_FILE = HOMEAI_DATA_DIR / "info_skill_feed_cache.json"

# A4.1.3 bottom status: current 24K spot-equivalent gold value in CNY/gram.
# This is intentionally independent from the hourly news Skill schedule.
GOLD_QUOTE_URL = os.getenv(
    "HOMEAI_GOLD_QUOTE_URL",
    "https://api.goldprice.dev/v1/carat?currency=CNY",
).strip()
GOLD_REFRESH_SEC = max(
    60,
    int(os.getenv("HOMEAI_GOLD_REFRESH_SEC", "300")),
)
GOLD_QUOTE_CACHE_FILE = HOMEAI_DATA_DIR / "gold_quote_cache.json"
GOLD_CNY_PER_GRAM: float | None = None
GOLD_QUOTE_UPDATED_AT = ""

@dataclass
class FeedItem:
    item_id: str
    category: str
    headline: str
    summary: str = ""
    source: str = ""
    source_url: str = ""
    priority: int = 0
    published_at: str = ""
    content_hash: str = ""

ACTIVE_SESSIONS: list["ClientSession"] = []
FEED_ITEMS: list[FeedItem] = []
FEED_ITEM_HISTORY: dict[str, FeedItem] = {}
FEED_REVISION = "boot"


HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
PORT = int(os.getenv("GATEWAY_PORT", "8765"))
WS_PATH = os.getenv("GATEWAY_PATH", "/companion")
MODE = os.getenv("P0_MODE", "full").strip().lower()

ASR_PROVIDER = os.getenv("ASR_PROVIDER", "volcengine").strip().lower()
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "volcengine").strip().lower()
ASR_FALLBACK = os.getenv("ASR_FALLBACK", "none").strip().lower()
TTS_FALLBACK = os.getenv("TTS_FALLBACK", "none").strip().lower()

# Volcengine / Doubao Speech, new console.
# One Speech API Key is used through X-Api-Key.
VOLCENGINE_API_KEY = os.getenv("VOLCENGINE_API_KEY", "").strip()

# Doubao Streaming ASR 2.0.
# Current device still uses PTT, so Mini buffers the utterance first, then feeds
# the buffered PCM to the streaming API in 200 ms frames. This removes the old
# auc_turbo dependency without changing StickS3 firmware.
VOLCENGINE_ASR_ENDPOINT = os.getenv(
    "VOLCENGINE_ASR_ENDPOINT",
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
).strip()
VOLCENGINE_ASR_RESOURCE_ID = os.getenv(
    "VOLCENGINE_ASR_RESOURCE_ID", "volc.seedasr.sauc.duration"
).strip()
VOLCENGINE_ASR_CHUNK_MS = int(os.getenv("VOLCENGINE_ASR_CHUNK_MS", "200"))
VOLCENGINE_ASR_SEND_INTERVAL_MS = int(
    os.getenv("VOLCENGINE_ASR_SEND_INTERVAL_MS", "100")
)
VOLCENGINE_ASR_ENABLE_NONSTREAM = (
    os.getenv("VOLCENGINE_ASR_ENABLE_NONSTREAM", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
VOLCENGINE_ASR_END_WINDOW_MS = int(
    os.getenv("VOLCENGINE_ASR_END_WINDOW_MS", "800")
)
VOLCENGINE_ASR_FORCE_TO_SPEECH_MS = int(
    os.getenv("VOLCENGINE_ASR_FORCE_TO_SPEECH_MS", "1000")
)
VOLCENGINE_ASR_CONNECT_TIMEOUT = float(
    os.getenv("VOLCENGINE_ASR_CONNECT_TIMEOUT", "8")
)
VOLCENGINE_ASR_FINAL_TIMEOUT = float(
    os.getenv("VOLCENGINE_ASR_FINAL_TIMEOUT", "12")
)

# V3 one-shot text / streamed audio WebSocket.
VOLCENGINE_TTS_ENDPOINT = os.getenv(
    "VOLCENGINE_TTS_ENDPOINT",
    "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream",
).strip()
VOLCENGINE_TTS_RESOURCE_ID = os.getenv(
    "VOLCENGINE_TTS_RESOURCE_ID", "seed-tts-2.0"
).strip()
VOLCENGINE_TTS_VOICE = os.getenv(
    "VOLCENGINE_TTS_VOICE", "zh_female_vv_uranus_bigtts"
).strip()
VOLCENGINE_TTS_SAMPLE_RATE = int(os.getenv("VOLCENGINE_TTS_SAMPLE_RATE", "16000"))
VOLCENGINE_TTS_SPEECH_RATE = int(os.getenv("VOLCENGINE_TTS_SPEECH_RATE", "0"))
VOLCENGINE_TTS_LOUDNESS_RATE = int(os.getenv("VOLCENGINE_TTS_LOUDNESS_RATE", "0"))
VOLCENGINE_TTS_CONNECT_TIMEOUT = float(os.getenv("VOLCENGINE_TTS_CONNECT_TIMEOUT", "8"))
VOLCENGINE_TTS_SESSION_TIMEOUT = float(os.getenv("VOLCENGINE_TTS_SESSION_TIMEOUT", "20"))

# Optional OpenAI speech fallback. Not required when domestic speech is selected.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "coral")
OPENAI_TTS_SPEED = float(os.getenv("OPENAI_TTS_SPEED", "1.05"))
OPENAI_TTS_INSTRUCTIONS = os.getenv(
    "OPENAI_TTS_INSTRUCTIONS",
    "自然、亲切、简洁的家庭AI助手语气。中文清楚，语速自然，不要过度播音腔。",
)
MAX_AGENT_CHARS = int(os.getenv("MAX_AGENT_CHARS", "600"))


def _best_effort_write_bytes(path: Path, data: bytes) -> None:
    try:
        path.write_bytes(data)
    except OSError as exc:
        print(f"[FILE-WARN] write_bytes failed path={path} error={type(exc).__name__}: {exc}")


def _best_effort_write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"[FILE-WARN] write_text failed path={path} error={type(exc).__name__}: {exc}")

OPENCLAW_BASE_URL = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18790").rstrip("/")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_MODEL = os.getenv("OPENCLAW_MODEL", "openclaw/default")
OPENCLAW_USER = os.getenv("OPENCLAW_USER", "home-ai-agent:main")
OPENCLAW_INFO_USER = os.getenv("OPENCLAW_INFO_USER", "home-ai-agent:info-feed")

MIC_RATE = 16000
MIC_CHANNELS = 1
MIC_SAMPLE_WIDTH = 2
TTS_CHUNK_BYTES = 8192
MAX_INPUT_BYTES = MIC_RATE * MIC_SAMPLE_WIDTH * 20  # 20 s hard server guard

# StickS3 firmware currently rejects a single TTS payload above 1.5 MiB.
# Keep each playback segment comfortably below that hard limit.
# 768 KiB @ PCM16 mono 16 kHz = 24.576 seconds.
DEVICE_TTS_SEGMENT_MAX_BYTES = int(
    os.getenv("DEVICE_TTS_SEGMENT_MAX_BYTES", str(768 * 1024))
)
DEVICE_TTS_PLAYBACK_MARGIN_SEC = float(
    os.getenv("DEVICE_TTS_PLAYBACK_MARGIN_SEC", "15")
)



def _display_category(category: str) -> str:
    value = str(category or "").strip().lower()
    if value == "game":
        return "游戏"
    if value == "finance":
        return "金融"
    return "资讯"


def _stable_feed_revision(items: list[FeedItem]) -> str:
    material = [
        {
            "id": item.item_id,
            "headline": item.headline,
            "content_hash": item.content_hash,
            "published_at": item.published_at,
        }
        for item in items
    ]
    raw = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _cache_payload(items: list[FeedItem], revision: str) -> dict[str, Any]:
    return {
        "protocol_version": INFO_SKILL_PROTOCOL,
        "saved_at": int(time.time()),
        "revision": revision,
        "items": [
            {
                "item_id": item.item_id,
                "category": item.category,
                "headline": item.headline,
                "summary": item.summary,
                "source": item.source,
                "source_url": item.source_url,
                "priority": item.priority,
                "published_at": item.published_at,
                "content_hash": item.content_hash,
            }
            for item in items
        ],
    }


def save_info_skill_cache() -> None:
    if not FEED_ITEMS:
        return
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = INFO_SKILL_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                _cache_payload(FEED_ITEMS, FEED_REVISION),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(INFO_SKILL_CACHE_FILE)
        print(
            f"[INFO-SKILL] last-good cache saved "
            f"count={len(FEED_ITEMS)} revision={FEED_REVISION}"
        )
    except OSError as exc:
        print(
            f"[INFO-WARN] cache write failed: "
            f"{type(exc).__name__}: {exc}"
        )


def load_info_skill_cache() -> bool:
    global FEED_ITEMS, FEED_REVISION

    if not INFO_SKILL_CACHE_FILE.exists():
        print("[INFO-SKILL] no last-good cache")
        return False

    try:
        body = json.loads(INFO_SKILL_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"[INFO-WARN] cache read failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False

    rows = body.get("items") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return False

    loaded: list[FeedItem] = []
    for raw in rows[:INFO_MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("item_id") or "").strip()
        headline = str(raw.get("headline") or "").strip()
        if not item_id or not headline:
            continue
        loaded.append(
            FeedItem(
                item_id=item_id[:96],
                category=str(raw.get("category") or "资讯")[:12],
                headline=headline[:160],
                summary=str(raw.get("summary") or "")[:1200],
                source=str(raw.get("source") or "")[:200],
                source_url=str(raw.get("source_url") or "")[:600],
                priority=int(raw.get("priority") or 0),
                published_at=str(raw.get("published_at") or "")[:64],
                content_hash=str(raw.get("content_hash") or "")[:128],
            )
        )

    if not loaded:
        return False

    FEED_ITEMS = loaded
    FEED_REVISION = str(body.get("revision") or _stable_feed_revision(loaded))
    for item in FEED_ITEMS:
        FEED_ITEM_HISTORY[item.item_id] = item

    print(
        f"[INFO-SKILL] last-good cache loaded "
        f"count={len(FEED_ITEMS)} revision={FEED_REVISION}"
    )
    return True


def _extract_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        raise ValueError("empty OpenClaw response")

    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()

    try:
        body = json.loads(value)
        if isinstance(body, dict):
            return body
    except json.JSONDecodeError:
        pass

    first = value.find("{")
    last = value.rfind("}")
    if first >= 0 and last > first:
        body = json.loads(value[first:last + 1])
        if isinstance(body, dict):
            return body

    raise ValueError("OpenClaw response is not a JSON object")


async def _openclaw_background_text(
    prompt: str,
    *,
    user: str = OPENCLAW_INFO_USER,
    timeout: float = 120,
) -> str:
    headers = {"Content-Type": "application/json"}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"

    payload = {
        "model": OPENCLAW_MODEL,
        "user": user,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await _post_with_retry(
            client,
            f"{OPENCLAW_BASE_URL}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        body = response.json()

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenClaw returned no choices: {body}")

    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )

    text = str(content).strip()
    if not text:
        raise RuntimeError("OpenClaw returned empty content")
    return text


def _build_get_feed_request() -> dict[str, Any]:
    return {
        "protocol_version": INFO_SKILL_PROTOCOL,
        "operation": "get_feed",
        "request_id": f"homeai-feed-{uuid.uuid4()}",
        "locale": "zh-CN",
        "timezone": "Asia/Taipei",
        "max_items": INFO_MAX_ITEMS,
        "category_limits": {
            "game": INFO_GAME_LIMIT,
            "finance": INFO_FINANCE_LIMIT,
        },
        "categories": ["game", "finance"],
        "max_age_hours": INFO_MAX_AGE_HOURS,
    }


async def call_homeai_info_get_feed() -> dict[str, Any]:
    request = _build_get_feed_request()

    prompt = f"""你现在执行 HomeAIAgent 的后台资讯任务，不是普通聊天。

[HOMEAI_INFO_CALL]
tool=homeai_info.get_feed
protocol=homeai-info/1.1
payload={json.dumps(request, ensure_ascii=False)}
[/HOMEAI_INFO_CALL]

执行规则：
1. 上面的 HOMEAI_INFO_CALL 是 HomeAIAgent 的固定后台触发标记。
2. 必须实际调用已经安装的 HomeAI Info Skill：homeai_info.get_feed。
3. payload 必须原样作为接口输入，不要自行改写字段含义。
4. 不要自己编造、补充或替代 Skill 的资讯。
5. 最终回复只允许是 Skill 返回的 JSON。
6. 不要 Markdown，不要代码围栏，不要解释，不要自然语言前后缀。
"""

    text = await _openclaw_background_text(prompt)
    body = _extract_json_object(text)

    if body.get("protocol_version") != INFO_SKILL_PROTOCOL:
        raise RuntimeError(
            "HomeAI Info protocol mismatch: "
            f"{body.get('protocol_version')!r}"
        )
    if body.get("operation") != "get_feed":
        raise RuntimeError(
            f"HomeAI Info operation mismatch: {body.get('operation')!r}"
        )
    return body


def normalize_skill_feed(body: dict[str, Any]) -> list[FeedItem]:
    status = str(body.get("status") or "").strip().lower()
    if status not in {"ok", "partial"}:
        err = body.get("error") or {}
        raise RuntimeError(
            "HomeAI Info get_feed failed: "
            f"{err.get('code') or status} "
            f"{err.get('message') or ''}".strip()
        )

    rows = body.get("items")
    if not isinstance(rows, list):
        raise RuntimeError("HomeAI Info items is not a list")

    result: list[FeedItem] = []
    seen_ids: set[str] = set()
    counts = {"game": 0, "finance": 0}

    for raw in rows:
        if not isinstance(raw, dict):
            continue

        raw_category = str(raw.get("category") or "").strip().lower()
        if raw_category not in counts:
            continue
        if counts[raw_category] >= (
            INFO_GAME_LIMIT if raw_category == "game" else INFO_FINANCE_LIMIT
        ):
            continue

        item_id = str(raw.get("id") or "").strip()
        headline = str(raw.get("headline") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        if not item_id or not headline or item_id in seen_ids:
            continue

        if len(headline) > INFO_HEADLINE_MAX_CHARS:
            print(
                f"[INFO-SKILL-WARN] headline over limit "
                f"id={item_id} chars={len(headline)} "
                f"max={INFO_HEADLINE_MAX_CHARS}; clamping"
            )
            headline = headline[:INFO_HEADLINE_MAX_CHARS]
        if len(summary) > INFO_SUMMARY_MAX_CHARS:
            print(
                f"[INFO-SKILL-WARN] summary over limit "
                f"id={item_id} chars={len(summary)} "
                f"max={INFO_SUMMARY_MAX_CHARS}; clamping"
            )
            summary = summary[:INFO_SUMMARY_MAX_CHARS]

        try:
            priority = int(raw.get("priority") or 0)
        except Exception:
            priority = 0
        priority = max(0, min(100, priority))

        item = FeedItem(
            item_id=item_id[:96],
            category=_display_category(raw_category),
            headline=headline,
            summary=summary,
            source=str(raw.get("source_name") or "")[:200],
            source_url=str(raw.get("source_url") or "")[:600],
            priority=priority,
            published_at=str(raw.get("published_at") or "")[:64],
            content_hash=str(raw.get("content_hash") or "")[:128],
        )

        result.append(item)
        seen_ids.add(item_id)
        counts[raw_category] += 1

        if len(result) >= INFO_MAX_ITEMS:
            break

    if not result:
        raise RuntimeError("HomeAI Info returned no usable items")

    print(
        f"[INFO-SKILL] normalized count={len(result)} "
        f"game={counts['game']} finance={counts['finance']} "
        f"status={status}"
    )

    warnings = body.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings[:5]:
            print(f"[INFO-SKILL-WARN] {warning}")

    return result


async def refresh_info_from_skill(*, force: bool = False) -> bool:
    global FEED_ITEMS, FEED_REVISION

    body = await call_homeai_info_get_feed()

    # A4.1.2 uses a fixed HomeAIAgent wall-clock schedule.
    # Keep the Skill field for protocol compatibility, but do not let it
    # override the user's 00/01/09..23 hourly schedule.
    suggested = body.get("next_refresh_after_sec")
    if suggested is not None:
        print(
            f"[INFO-SKILL] skill suggested next={suggested}s; "
            "ignored by fixed hourly schedule"
        )

    new_items = normalize_skill_feed(body)
    new_revision = _stable_feed_revision(new_items)

    if not force and new_revision == FEED_REVISION:
        print(
            f"[INFO-SKILL] unchanged revision={new_revision}"
        )
        return False

    FEED_ITEMS = new_items
    FEED_REVISION = new_revision

    for item in FEED_ITEMS:
        FEED_ITEM_HISTORY[item.item_id] = item

    # Keep enough history so an in-flight PTT turn can still resolve an item
    # even if a feed refresh just replaced the visible list.
    if len(FEED_ITEM_HISTORY) > 160:
        keep_ids = {item.item_id for item in FEED_ITEMS}
        old_keys = list(FEED_ITEM_HISTORY.keys())
        for key in old_keys:
            if len(FEED_ITEM_HISTORY) <= 100:
                break
            if key not in keep_ids:
                FEED_ITEM_HISTORY.pop(key, None)

    save_info_skill_cache()

    print(
        f"[INFO-SKILL] refreshed count={len(FEED_ITEMS)} "
        f"revision={FEED_REVISION}"
    )
    return True


def _feed_item_by_id(item_id: str) -> FeedItem | None:
    for item in FEED_ITEMS:
        if item.item_id == item_id:
            return item
    return FEED_ITEM_HISTORY.get(item_id)


def enrich_info_context(context: dict[str, Any]) -> dict[str, Any]:
    enriched: dict[str, Any] = {}
    for key in ("current", "previous", "next"):
        src = dict(context.get(key) or {})
        item = _feed_item_by_id(str(src.get("item_id") or ""))
        if item:
            src["summary"] = item.summary
            src["source"] = item.source
            src["source_url"] = item.source_url
            src["published_at"] = item.published_at
            src["priority"] = item.priority
        enriched[key] = src
    return enriched


def gold_status_text() -> str:
    if GOLD_CNY_PER_GRAM is None:
        return "金 --/g"
    return f"金 ¥{GOLD_CNY_PER_GRAM:.1f}/g"


def load_gold_quote_cache() -> bool:
    global GOLD_CNY_PER_GRAM, GOLD_QUOTE_UPDATED_AT

    if not GOLD_QUOTE_CACHE_FILE.exists():
        return False

    try:
        body = json.loads(GOLD_QUOTE_CACHE_FILE.read_text(encoding="utf-8"))
        value = float(body.get("cny_per_gram"))
        if not 100.0 <= value <= 5000.0:
            raise ValueError(f"out-of-range gold quote={value}")
        GOLD_CNY_PER_GRAM = value
        GOLD_QUOTE_UPDATED_AT = str(body.get("updated_at") or "")
        print(
            f"[GOLD] cache loaded price={GOLD_CNY_PER_GRAM:.2f} CNY/g "
            f"updated_at={GOLD_QUOTE_UPDATED_AT}"
        )
        return True
    except Exception as exc:
        print(
            f"[GOLD-WARN] cache read failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def save_gold_quote_cache() -> None:
    if GOLD_CNY_PER_GRAM is None:
        return
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = GOLD_QUOTE_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "source": "goldprice.dev",
                    "kind": "24k_spot_equivalent",
                    "currency": "CNY",
                    "unit": "gram",
                    "cny_per_gram": GOLD_CNY_PER_GRAM,
                    "updated_at": GOLD_QUOTE_UPDATED_AT,
                    "saved_at": int(time.time()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(GOLD_QUOTE_CACHE_FILE)
    except OSError as exc:
        print(f"[GOLD-WARN] cache write failed: {exc}")


async def fetch_gold_quote() -> tuple[float, str]:
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(
            GOLD_QUOTE_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "HomeAIAgent/0.4.1.3",
            },
        )
        response.raise_for_status()
        body = response.json()

    raw_value = body.get("price_gram_24k")
    if raw_value is None:
        raise RuntimeError("gold quote response missing price_gram_24k")

    value = float(raw_value)
    if not 100.0 <= value <= 5000.0:
        raise RuntimeError(f"gold quote out of range: {value}")

    updated_at = str(
        body.get("timestamp")
        or body.get("updated_at")
        or datetime.now(_info_schedule_tz()).isoformat()
    )
    return value, updated_at


async def refresh_gold_quote(*, force_sync: bool = False) -> bool:
    global GOLD_CNY_PER_GRAM, GOLD_QUOTE_UPDATED_AT

    old_value = GOLD_CNY_PER_GRAM
    value, updated_at = await fetch_gold_quote()

    GOLD_CNY_PER_GRAM = value
    GOLD_QUOTE_UPDATED_AT = updated_at
    save_gold_quote_cache()

    changed = (
        old_value is None
        or abs(value - old_value) >= 0.05
    )

    print(
        f"[GOLD] price={value:.2f} CNY/g "
        f"changed={changed} updated_at={updated_at}"
    )

    # Frames are pre-rendered on Mini. If the quote changes, resend the same
    # feed so only the bottom status bar visually updates on Glass2.
    if FEED_ITEMS and (changed or force_sync):
        await broadcast_info_sync_when_idle()

    return changed


async def gold_quote_loop() -> None:
    # Fetch once immediately, then keep the status current independently of
    # the hourly news schedule (including the 01:00-09:00 news quiet window).
    while True:
        try:
            await refresh_gold_quote()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Keep last-good quote on transient provider/network failure.
            print(
                f"[GOLD-WARN] refresh failed, keeping last-good quote: "
                f"{type(exc).__name__}: {exc}"
            )

        await asyncio.sleep(GOLD_REFRESH_SEC)


def _info_schedule_tz() -> ZoneInfo:
    try:
        return ZoneInfo(INFO_SCHEDULE_TIMEZONE)
    except Exception:
        print(
            f"[INFO-SKILL-WARN] invalid timezone={INFO_SCHEDULE_TIMEZONE!r}; "
            "falling back to Asia/Taipei"
        )
        return ZoneInfo("Asia/Taipei")


def next_info_poll_at(now: datetime | None = None) -> datetime:
    tz = _info_schedule_tz()
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    # Allowed exact hours: 00, 01, 09..23.
    # Therefore after 01:00 the next slot is 09:00.
    while candidate.hour not in INFO_ALLOWED_HOURS:
        candidate += timedelta(hours=1)

    return candidate


def seconds_until_next_info_poll(now: datetime | None = None) -> tuple[float, datetime]:
    tz = _info_schedule_tz()
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    target = next_info_poll_at(now)
    return max(0.0, (target - now).total_seconds()), target


def display_sleep_window_active(now: datetime | None = None) -> bool:
    tz = _info_schedule_tz()
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    minute_of_day = now.hour * 60 + now.minute
    sleep_minute = DISPLAY_SLEEP_HOUR * 60 + DISPLAY_SLEEP_MINUTE
    wake_minute = DISPLAY_WAKE_HOUR * 60 + DISPLAY_WAKE_MINUTE
    return sleep_minute <= minute_of_day < wake_minute


def next_display_transition_at(now: datetime | None = None) -> datetime:
    tz = _info_schedule_tz()
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    sleep_today = now.replace(
        hour=DISPLAY_SLEEP_HOUR,
        minute=DISPLAY_SLEEP_MINUTE,
        second=0,
        microsecond=0,
    )
    wake_today = now.replace(
        hour=DISPLAY_WAKE_HOUR,
        minute=DISPLAY_WAKE_MINUTE,
        second=0,
        microsecond=0,
    )

    if now < sleep_today:
        return sleep_today
    if now < wake_today:
        return wake_today
    return sleep_today + timedelta(days=1)


async def send_display_policy(session: "ClientSession") -> None:
    sleeping = display_sleep_window_active()
    await send_json(
        session.ws,
        {
            "type": "display.sleep" if sleeping else "display.wake",
            "reason": "night_schedule",
            "timezone": INFO_SCHEDULE_TIMEZONE,
        },
    )
    print(
        f"[DISPLAY] policy sent "
        f"{'SLEEP' if sleeping else 'WAKE'}"
    )


async def broadcast_display_policy() -> None:
    for session in list(ACTIVE_SESSIONS):
        try:
            await send_display_policy(session)
        except Exception as exc:
            print(
                f"[DISPLAY-WARN] policy send failed: "
                f"{type(exc).__name__}: {exc}"
            )


async def display_schedule_loop() -> None:
    # Exact night window: 01:05 <= local time < 09:00.
    while True:
        now = datetime.now(_info_schedule_tz())
        target = next_display_transition_at(now)
        wait_sec = max(0.0, (target - now).total_seconds())

        print(
            f"[DISPLAY] next transition={target.isoformat()} "
            f"wait={int(wait_sec)}s"
        )
        await asyncio.sleep(wait_sec)

        try:
            await broadcast_display_policy()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[DISPLAY-WARN] transition failed: "
                f"{type(exc).__name__}: {exc}"
            )


def _sessions_busy() -> bool:
    return any(
        session.recording
        or session.processing
        or session.playback_sequence_active
        for session in ACTIVE_SESSIONS
    )


async def _wait_for_idle(max_wait_sec: int = 120) -> bool:
    for _ in range(max_wait_sec):
        if not _sessions_busy():
            return True
        await asyncio.sleep(1)
    return not _sessions_busy()


async def broadcast_info_sync_when_idle() -> None:
    if not await _wait_for_idle():
        print("[INFO-SKILL] device sync deferred: voice path still busy")
        return

    for session in list(ACTIVE_SESSIONS):
        try:
            await send_info_sync(session)
        except Exception as exc:
            print(
                f"[INFO-WARN] info sync failed: "
                f"{type(exc).__name__}: {exc}"
            )


async def info_skill_poll_loop() -> None:
    # Fixed wall-clock schedule:
    # 00:00, 01:00, then 09:00 through 23:00 every hour.
    # The long overnight pause is intentional.
    while True:
        wait_sec, target = seconds_until_next_info_poll()
        print(
            f"[INFO-SKILL] next scheduled poll="
            f"{target.isoformat()} wait={int(wait_sec)}s"
        )

        await asyncio.sleep(wait_sec)

        try:
            if not await _wait_for_idle():
                print(
                    "[INFO-SKILL] scheduled poll skipped: "
                    "voice path busy too long"
                )
                continue

            print(
                f"[INFO-SKILL] scheduled poll start="
                f"{datetime.now(_info_schedule_tz()).isoformat()}"
            )
            changed = await refresh_info_from_skill(force=False)
            if changed:
                await broadcast_info_sync_when_idle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never clear last-good cache on OpenClaw/Skill failure.
            print(
                f"[INFO-SKILL-ERROR] scheduled refresh failed, "
                f"keeping last-good cache: "
                f"{type(exc).__name__}: {exc}"
            )


def _font_candidates() -> list[Path]:
    return [
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/arphic-gbsn00lp/gbsn00lp.ttf"),
    ]


def _load_cjk_font(size: int, *, bold: bool = False):
    candidates = _font_candidates()
    if bold:
        candidates = [
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ] + candidates

    for path in candidates:
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue

    print("[INFO-WARN] no CJK system font found; using Pillow default")
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return max(0, box[2] - box[0])


def _fit_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
) -> tuple[str, str]:
    out = ""
    for idx, ch in enumerate(text):
        candidate = out + ch
        if _text_width(draw, candidate, font) > max_width:
            return out, text[idx:]
        out = candidate
    return out, ""


def _three_line_headline(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
) -> tuple[str, str, str]:
    text = " ".join(text.strip().split())

    line1, rest = _fit_line(draw, text, font, 124)
    if not rest:
        return line1, "", ""

    line2, rest = _fit_line(draw, rest, font, 124)
    if not rest:
        return line1, line2, ""

    line3, remainder = _fit_line(draw, rest, font, 124)
    if not remainder:
        return line1, line2, line3

    ellipsis = "…"
    while line3 and _text_width(draw, line3 + ellipsis, font) > 124:
        line3 = line3[:-1]
    return line1, line2, line3 + ellipsis


def render_feed_frame(item: FeedItem, index: int, total: int) -> bytes:
    image = Image.new("1", (INFO_FRAME_WIDTH, INFO_FRAME_HEIGHT), 0)
    draw = ImageDraw.Draw(image)

    # A4.2: category/header is intentionally NOT rendered.
    # The full upper 47 px are used for three readable headline lines.
    body_font = _load_cjk_font(11)
    small_font = ImageFont.load_default()

    line1, line2, line3 = _three_line_headline(
        draw,
        item.headline,
        body_font,
    )

    if line1:
        draw.text((2, 2), line1, font=body_font, fill=1)
    if line2:
        draw.text((2, 16), line2, font=body_font, fill=1)
    if line3:
        draw.text((2, 30), line3, font=body_font, fill=1)

    draw.line((0, 47, 127, 47), fill=1)

    # Bottom-left: current item / total. Example: 03/20.
    index_text = f"{index + 1:02d}/{total:02d}"
    draw.text((2, 53), index_text, font=small_font, fill=1)

    # Bottom-right: live RMB 24K spot-equivalent gold quote.
    status_font = _load_cjk_font(9)
    gold_text = gold_status_text()
    gold_box = draw.textbbox((0, 0), gold_text, font=status_font)
    gold_width = max(0, gold_box[2] - gold_box[0])
    draw.text(
        (126 - gold_width, 51),
        gold_text,
        font=status_font,
        fill=1,
    )

    data = image.tobytes()
    if len(data) != INFO_FRAME_WIDTH * INFO_FRAME_HEIGHT // 8:
        raise RuntimeError(f"invalid rendered frame size={len(data)}")
    return data


async def send_info_sync(session: "ClientSession") -> None:
    if not FEED_ITEMS:
        return

    await send_json(
        session.ws,
        {
            "type": "info.begin",
            "revision": FEED_REVISION,
            "count": len(FEED_ITEMS),
        },
    )

    total = len(FEED_ITEMS)
    for idx, item in enumerate(FEED_ITEMS):
        frame = render_feed_frame(item, idx, total)
        await send_json(
            session.ws,
            {
                "type": "info.item",
                "revision": FEED_REVISION,
                "index": idx,
                "id": item.item_id,
                "category": item.category,
                "headline": item.headline,
                "frame_hex": frame.hex(),
            },
        )

    await send_json(
        session.ws,
        {
            "type": "info.end",
            "revision": FEED_REVISION,
            "count": total,
        },
    )
    print(f"[INFO] sync sent revision={FEED_REVISION} count={total}")


@dataclass
class ClientSession:
    ws: Any
    audio: bytearray = field(default_factory=bytearray)
    context: dict[str, Any] = field(default_factory=dict)
    diag_glass_mode: str = "GLASS NORMAL"
    recording: bool = False
    processing: bool = False
    playback_sequence_active: bool = False
    playback_done_event: asyncio.Event = field(default_factory=asyncio.Event)
    playback_error_event: asyncio.Event = field(default_factory=asyncio.Event)
    playback_slot_ready_event: asyncio.Event = field(default_factory=asyncio.Event)


async def send_json(ws, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, ensure_ascii=False))


async def send_state(ws, state: str) -> None:
    await send_json(ws, {"type": "assistant.state", "state": state})


def pcm_level_stats(pcm: bytes) -> dict[str, float]:
    """Return PCM16 level diagnostics plus a rough speech/noise separation.

    The noise/speech figures are diagnostic only. Audio is NOT normalized or gated.
    We split the clip into 20 ms frames, use a low percentile as an estimated
    background floor and a high percentile as an estimated active-speech level.
    """
    if not pcm:
        return {
            "peak": 0.0, "rms": 0.0, "peak_dbfs": -120.0, "rms_dbfs": -120.0,
            "clip_pct": 0.0, "noise_dbfs": -120.0, "speech_dbfs": -120.0, "snr_db": 0.0
        }

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if os.sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {
            "peak": 0.0, "rms": 0.0, "peak_dbfs": -120.0, "rms_dbfs": -120.0,
            "clip_pct": 0.0, "noise_dbfs": -120.0, "speech_dbfs": -120.0, "snr_db": 0.0
        }

    peak = max(abs(int(v)) for v in samples)
    mean_sq = sum(float(v) * float(v) for v in samples) / len(samples)
    rms = math.sqrt(mean_sq)

    def dbfs(v: float) -> float:
        if v <= 0.0:
            return -120.0
        return 20.0 * math.log10(v / 32767.0)

    clipped = sum(1 for v in samples if abs(int(v)) >= 32760)

    # 20 ms RMS frames at 16 kHz.
    frame_len = max(1, int(MIC_RATE * 0.020))
    frame_rms = []
    for start in range(0, len(samples) - frame_len + 1, frame_len):
        frame = samples[start:start + frame_len]
        e = sum(float(v) * float(v) for v in frame) / len(frame)
        frame_rms.append(math.sqrt(e))

    if frame_rms:
        ordered = sorted(frame_rms)
        def pct(p: float) -> float:
            idx = int(round((len(ordered) - 1) * p))
            return ordered[max(0, min(len(ordered) - 1, idx))]
        noise = pct(0.20)
        speech = pct(0.90)
    else:
        noise = rms
        speech = rms

    noise_db = dbfs(noise)
    speech_db = dbfs(speech)
    snr = max(0.0, speech_db - noise_db)

    return {
        "peak": float(peak),
        "rms": float(rms),
        "peak_dbfs": dbfs(float(peak)),
        "rms_dbfs": dbfs(rms),
        "clip_pct": (100.0 * clipped / len(samples)),
        "noise_dbfs": noise_db,
        "speech_dbfs": speech_db,
        "snr_db": snr,
    }


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = MIC_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out.getvalue()


def wav_to_pcm16_mono(wav_bytes: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if width != 2:
        raise RuntimeError(f"TTS WAV must be PCM16; got sample width {width}")
    if channels == 1:
        return frames, rate
    if channels != 2:
        raise RuntimeError(f"Unsupported TTS channel count: {channels}")

    # Simple stereo -> mono average, no external DSP dependency.
    import array
    samples = array.array("h")
    samples.frombytes(frames)
    if os.sys.byteorder != "little":
        samples.byteswap()
    mono = array.array("h")
    for i in range(0, len(samples) - 1, 2):
        mono.append((int(samples[i]) + int(samples[i + 1])) // 2)
    if os.sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes(), rate


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = 2,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt >= attempts:
                raise
            await asyncio.sleep(0.35 * attempt)
    assert last_exc is not None
    raise last_exc



def _provider_enabled(name: str) -> bool:
    return name in {"volcengine", "openai"}


def _volcengine_has_credentials() -> bool:
    return bool(VOLCENGINE_API_KEY)


def _volcengine_asr_headers(resource_id: str) -> dict[str, str]:
    if not VOLCENGINE_API_KEY:
        raise RuntimeError("VOLCENGINE_API_KEY is empty")
    connect_id = str(uuid.uuid4())
    return {
        "X-Api-Key": VOLCENGINE_API_KEY,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": connect_id,
        # Kept for compatibility with current new-console examples.
        "X-Api-Request-Id": connect_id,
        "X-Api-Sequence": "-1",
    }


def _volcengine_tts_headers(resource_id: str) -> dict[str, str]:
    if not VOLCENGINE_API_KEY:
        raise RuntimeError("VOLCENGINE_API_KEY is empty")
    return {
        "X-Api-Key": VOLCENGINE_API_KEY,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": str(uuid.uuid4()),
        "X-Control-Require-Usage-Tokens-Return": "*",
    }


def _http_error_detail(response: httpx.Response) -> str:
    text = response.text.strip()
    if len(text) > 600:
        text = text[:600] + "..."
    return (
        f"HTTP {response.status_code}; "
        f"request_id={response.headers.get('X-Api-Request-Id','')}; "
        f"logid={response.headers.get('X-Tt-Logid','')}; "
        f"body={text}"
    )


# Volcengine Streaming ASR V1 binary framing.
_VOLC_ASR_FULL_CLIENT_HEADER = bytes((0x11, 0x10, 0x11, 0x00))
_VOLC_ASR_AUDIO_HEADER = bytes((0x11, 0x20, 0x01, 0x00))
_VOLC_ASR_AUDIO_LAST_HEADER = bytes((0x11, 0x22, 0x01, 0x00))

_VOLC_ASR_MSG_FULL_SERVER = 0x09
_VOLC_ASR_MSG_ERROR = 0x0F


def _volc_asr_full_request_packet(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = gzip.compress(raw)
    return (
        _VOLC_ASR_FULL_CLIENT_HEADER
        + struct.pack(">I", len(compressed))
        + compressed
    )


def _volc_asr_audio_packet(audio: bytes, *, last: bool) -> bytes:
    compressed = gzip.compress(audio)
    header = _VOLC_ASR_AUDIO_LAST_HEADER if last else _VOLC_ASR_AUDIO_HEADER
    return header + struct.pack(">I", len(compressed)) + compressed


def _volc_asr_parse_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        raise RuntimeError(f"Volcengine ASR short frame: {len(data)} bytes")

    version = data[0] >> 4
    header_words = data[0] & 0x0F
    header_size = header_words * 4
    msg_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F

    if version != 1 or header_size < 4 or header_size > len(data):
        raise RuntimeError(
            f"Volcengine ASR bad header version={version} size={header_size}"
        )

    pos = header_size
    result: dict[str, Any] = {
        "msg_type": msg_type,
        "flags": flags,
        "sequence": None,
        "payload": None,
        "is_last": flags in (0x02, 0x03),
        "error_code": None,
    }

    if msg_type == _VOLC_ASR_MSG_ERROR:
        if pos + 8 > len(data):
            raise RuntimeError("Volcengine ASR malformed error frame")
        error_code = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        payload_size = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        payload = data[pos:pos + payload_size]
        if compression == 1 and payload:
            payload = gzip.decompress(payload)
        result["error_code"] = error_code
        result["payload"] = _volc_decode_json_or_text(payload)
        return result

    if msg_type != _VOLC_ASR_MSG_FULL_SERVER:
        result["payload"] = data[pos:]
        return result

    # Sequence exists only when the positive/negative sequence flag is set.
    if flags in (0x01, 0x03):
        if pos + 4 > len(data):
            raise RuntimeError("Volcengine ASR malformed response sequence")
        result["sequence"] = struct.unpack_from(">i", data, pos)[0]
        pos += 4

    if pos + 4 > len(data):
        raise RuntimeError("Volcengine ASR malformed response payload length")
    payload_size = struct.unpack_from(">I", data, pos)[0]
    pos += 4

    if pos + payload_size > len(data):
        raise RuntimeError(
            f"Volcengine ASR truncated payload size={payload_size} "
            f"remaining={len(data)-pos}"
        )

    payload = data[pos:pos + payload_size]
    if compression == 1 and payload:
        payload = gzip.decompress(payload)

    if serialization == 1:
        result["payload"] = _volc_decode_json_or_text(payload)
    else:
        result["payload"] = payload
    return result


def _volc_asr_extract_text(body: Any) -> tuple[str, bool]:
    if not isinstance(body, dict):
        return "", False
    result = body.get("result") or {}
    if not isinstance(result, dict):
        return "", False

    text = str(result.get("text", "") or "").strip()
    definite = False

    utterances = result.get("utterances") or []
    if isinstance(utterances, list):
        definite = any(
            isinstance(item, dict) and bool(item.get("definite"))
            for item in utterances
        )

    return text, definite


async def volcengine_transcribe(wav_bytes: bytes) -> str:
    """Doubao Streaming ASR 2.0 over optimized bidirectional WebSocket.

    The StickS3 currently sends a complete PTT utterance to Mini. Mini converts
    that WAV back to PCM16 and feeds it to the streaming service in 200 ms
    packets. This is intentionally server-side only, so no firmware reflash is
    needed for the ASR 2.0 migration.
    """
    pcm, rate = wav_to_pcm16_mono(wav_bytes)
    if rate != MIC_RATE:
        raise RuntimeError(
            f"Volcengine ASR requires {MIC_RATE} Hz PCM; got {rate} Hz"
        )
    if not pcm:
        raise RuntimeError("Volcengine ASR received empty PCM")

    chunk_bytes = max(
        2,
        int(MIC_RATE * MIC_SAMPLE_WIDTH * VOLCENGINE_ASR_CHUNK_MS / 1000),
    )
    chunk_bytes -= chunk_bytes % 2
    chunks = [
        pcm[pos:pos + chunk_bytes]
        for pos in range(0, len(pcm), chunk_bytes)
    ]

    headers = _volcengine_asr_headers(VOLCENGINE_ASR_RESOURCE_ID)
    request = {
        "user": {
            "uid": "home-ai-agent",
            "platform": "macos",
            "app_version": "p0-a3.6",
        },
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": MIC_RATE,
            "bits": 16,
            "channel": 1,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_nonstream": VOLCENGINE_ASR_ENABLE_NONSTREAM,
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": True,
            "result_type": "full",
            "enable_accelerate_text": False,
            "end_window_size": VOLCENGINE_ASR_END_WINDOW_MS,
            "force_to_speech_time": VOLCENGINE_ASR_FORCE_TO_SPEECH_MS,
        },
    }

    best_text = ""
    final_text = ""
    logid = ""

    try:
        async with websockets.connect(
            VOLCENGINE_ASR_ENDPOINT,
            additional_headers=headers,
            ping_interval=None,
            open_timeout=VOLCENGINE_ASR_CONNECT_TIMEOUT,
            close_timeout=2,
            max_size=None,
        ) as ws:
            # Capture response log id if the installed websockets version exposes it.
            response = getattr(ws, "response", None)
            response_headers = getattr(response, "headers", None)
            if response_headers is not None:
                logid = str(response_headers.get("X-Tt-Logid", "") or "")
            if logid:
                print(f"[ASR:volcengine] connected logid={logid}")

            await ws.send(_volc_asr_full_request_packet(request))

            sender_done = asyncio.Event()

            async def sender() -> None:
                try:
                    for idx, chunk in enumerate(chunks):
                        is_last = idx == len(chunks) - 1
                        await ws.send(_volc_asr_audio_packet(chunk, last=is_last))
                        if not is_last and VOLCENGINE_ASR_SEND_INTERVAL_MS > 0:
                            await asyncio.sleep(
                                VOLCENGINE_ASR_SEND_INTERVAL_MS / 1000.0
                            )
                finally:
                    sender_done.set()

            send_task = asyncio.create_task(sender())

            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(),
                            timeout=VOLCENGINE_ASR_FINAL_TIMEOUT,
                        )
                    except asyncio.TimeoutError as exc:
                        if best_text:
                            print(
                                "[ASR:volcengine] final timeout; "
                                "using best available transcript"
                            )
                            final_text = best_text
                            break
                        raise RuntimeError(
                            f"Volcengine ASR final timeout "
                            f"({VOLCENGINE_ASR_FINAL_TIMEOUT:.0f}s) "
                            f"logid={logid}"
                        ) from exc

                    if isinstance(raw, str):
                        # Current ASR protocol is binary. Keep text frames for debug.
                        try:
                            body = json.loads(raw)
                        except json.JSONDecodeError:
                            body = {"text_frame": raw}
                        text, definite = _volc_asr_extract_text(body)
                        if text:
                            best_text = text
                        if definite:
                            final_text = text or best_text
                        continue

                    frame = _volc_asr_parse_frame(bytes(raw))

                    if frame["msg_type"] == _VOLC_ASR_MSG_ERROR:
                        raise RuntimeError(
                            f"Volcengine ASR error code={frame['error_code']} "
                            f"logid={logid} payload={frame['payload']}"
                        )

                    body = frame.get("payload")
                    text, definite = _volc_asr_extract_text(body)
                    if text:
                        best_text = text
                    if definite and text:
                        final_text = text

                    if frame.get("is_last"):
                        if not final_text:
                            final_text = best_text
                        break
            finally:
                await send_task

    except websockets.InvalidStatus as exc:
        raise RuntimeError(
            f"Volcengine ASR WebSocket handshake failed "
            f"resource={VOLCENGINE_ASR_RESOURCE_ID}: {exc}"
        ) from exc

    transcript = (final_text or best_text).strip()
    if not transcript:
        raise RuntimeError(
            f"Volcengine ASR returned empty transcript logid={logid or 'unknown'}"
        )
    return transcript


# Volcengine V3 binary protocol constants for unidirectional WebSocket TTS.
_VOLC_TTS_HEADER_SEND_TEXT = bytes((0x11, 0x10, 0x10, 0x00))
_VOLC_TTS_HEADER_WITH_EVENT = bytes((0x11, 0x14, 0x10, 0x00))
_VOLC_TTS_MSG_FULL_SERVER = 0x94
_VOLC_TTS_MSG_AUDIO_ONLY = 0xB4
_VOLC_TTS_MSG_ERROR = 0xF0

_VOLC_TTS_EVENT_FINISH_CONNECTION = 2
_VOLC_TTS_EVENT_CONNECTION_FINISHED = 52
_VOLC_TTS_EVENT_SESSION_FINISHED = 152
_VOLC_TTS_EVENT_SESSION_FAILED = 153
_VOLC_TTS_EVENT_SENTENCE_START = 350
_VOLC_TTS_EVENT_SENTENCE_END = 351
_VOLC_TTS_EVENT_AUDIO = 352


def _volc_tts_send_text_packet(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _VOLC_TTS_HEADER_SEND_TEXT + struct.pack(">I", len(raw)) + raw


def _volc_tts_finish_packet() -> bytes:
    payload = b"{}"
    return (
        _VOLC_TTS_HEADER_WITH_EVENT
        + struct.pack(">i", _VOLC_TTS_EVENT_FINISH_CONNECTION)
        + struct.pack(">I", len(payload))
        + payload
    )


def _volc_read_lp(data: bytes, pos: int) -> tuple[bytes, int]:
    if pos + 4 > len(data):
        raise RuntimeError("Volcengine TTS malformed frame: missing length prefix")
    size = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    end = pos + size
    if end > len(data):
        raise RuntimeError(
            f"Volcengine TTS malformed frame: payload size={size} remaining={len(data)-pos}"
        )
    return data[pos:end], end


def _volc_decode_json_or_text(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _volc_tts_parse_frame(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "msg_type": 0,
        "event": 0,
        "session_id": "",
        "connection_id": "",
        "payload": None,
        "audio": b"",
        "error_code": 0,
    }
    if len(data) < 4:
        raise RuntimeError(f"Volcengine TTS short frame: {len(data)} bytes")

    msg_type = data[1]
    result["msg_type"] = msg_type
    pos = 4

    if msg_type == _VOLC_TTS_MSG_ERROR:
        if pos + 4 > len(data):
            raise RuntimeError("Volcengine TTS malformed error frame")
        error_code = struct.unpack_from(">i", data, pos)[0]
        pos += 4
        raw, pos = _volc_read_lp(data, pos)
        result["error_code"] = error_code
        result["event"] = error_code
        result["payload"] = _volc_decode_json_or_text(raw)
        return result

    if pos + 4 > len(data):
        raise RuntimeError("Volcengine TTS malformed server frame: missing event")
    event = struct.unpack_from(">i", data, pos)[0]
    pos += 4
    result["event"] = event

    if event == _VOLC_TTS_EVENT_CONNECTION_FINISHED:
        raw, pos = _volc_read_lp(data, pos)
        result["connection_id"] = raw.decode("utf-8", errors="replace")
        if pos < len(data):
            raw, pos = _volc_read_lp(data, pos)
            result["payload"] = _volc_decode_json_or_text(raw)
        return result

    raw, pos = _volc_read_lp(data, pos)
    result["session_id"] = raw.decode("utf-8", errors="replace")

    if msg_type == _VOLC_TTS_MSG_AUDIO_ONLY:
        raw, pos = _volc_read_lp(data, pos)
        result["audio"] = raw
        return result

    if msg_type == _VOLC_TTS_MSG_FULL_SERVER:
        if pos < len(data):
            raw, pos = _volc_read_lp(data, pos)
            result["payload"] = _volc_decode_json_or_text(raw)
        return result

    return result


async def volcengine_tts_pcm(text: str) -> tuple[bytes, int]:
    """Doubao V3 unidirectional WebSocket TTS -> raw PCM16 mono."""
    if VOLCENGINE_TTS_SAMPLE_RATE not in {
        8000, 16000, 22050, 24000, 32000, 44100, 48000
    }:
        raise RuntimeError(
            f"Unsupported VOLCENGINE_TTS_SAMPLE_RATE={VOLCENGINE_TTS_SAMPLE_RATE}"
        )
    if not text.strip():
        raise RuntimeError("TTS text is empty")
    if not VOLCENGINE_TTS_VOICE:
        raise RuntimeError("VOLCENGINE_TTS_VOICE is empty")

    headers = _volcengine_tts_headers(VOLCENGINE_TTS_RESOURCE_ID)
    payload = {
        "user": {"uid": "home-ai-agent"},
        "req_params": {
            "speaker": VOLCENGINE_TTS_VOICE,
            "audio_params": {
                "format": "pcm",
                "sample_rate": VOLCENGINE_TTS_SAMPLE_RATE,
                "speech_rate": VOLCENGINE_TTS_SPEECH_RATE,
                "loudness_rate": VOLCENGINE_TTS_LOUDNESS_RATE,
            },
            "text": text[:600],
        },
    }

    chunks: list[bytes] = []
    session_id = ""

    try:
        async with websockets.connect(
            VOLCENGINE_TTS_ENDPOINT,
            additional_headers=headers,
            ping_interval=None,
            open_timeout=VOLCENGINE_TTS_CONNECT_TIMEOUT,
            close_timeout=2,
            max_size=None,
        ) as ws:
            await ws.send(_volc_tts_send_text_packet(payload))

            while True:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=VOLCENGINE_TTS_SESSION_TIMEOUT,
                    )
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"Volcengine TTS idle timeout "
                        f"({VOLCENGINE_TTS_SESSION_TIMEOUT:.0f}s)"
                    ) from exc

                if isinstance(raw, str):
                    print(f"[TTS:volcengine] unexpected text frame: {raw[:300]}")
                    continue

                frame = _volc_tts_parse_frame(bytes(raw))
                if frame["session_id"] and not session_id:
                    session_id = str(frame["session_id"])

                if frame["msg_type"] == _VOLC_TTS_MSG_ERROR:
                    raise RuntimeError(
                        f"Volcengine TTS error code={frame['error_code']} "
                        f"payload={frame['payload']}"
                    )

                event = int(frame["event"])
                if event == _VOLC_TTS_EVENT_SESSION_FAILED:
                    raise RuntimeError(
                        f"Volcengine TTS SessionFailed "
                        f"session={session_id} payload={frame['payload']}"
                    )

                if event == _VOLC_TTS_EVENT_AUDIO and frame["audio"]:
                    chunks.append(bytes(frame["audio"]))

                if event == _VOLC_TTS_EVENT_SESSION_FINISHED:
                    try:
                        await ws.send(_volc_tts_finish_packet())
                    except Exception:
                        pass
                    break

    except websockets.InvalidStatus as exc:
        raise RuntimeError(
            f"Volcengine TTS WebSocket handshake failed: {exc}"
        ) from exc

    pcm = b"".join(chunks)
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if not pcm:
        raise RuntimeError(
            f"Volcengine TTS returned no PCM audio session={session_id or 'unknown'}"
        )
    return pcm, VOLCENGINE_TTS_SAMPLE_RATE


async def transcribe_with_provider(provider: str, wav_bytes: bytes) -> str:
    if provider == "volcengine":
        return await volcengine_transcribe(wav_bytes)
    if provider == "openai":
        return await openai_transcribe(wav_bytes)
    raise RuntimeError(f"Unknown ASR provider: {provider}")


async def synthesize_with_provider(provider: str, text: str) -> tuple[bytes, int]:
    if provider == "volcengine":
        return await volcengine_tts_pcm(text)
    if provider == "openai":
        wav_bytes = await openai_tts_wav(text)
        return wav_to_pcm16_mono(wav_bytes)
    raise RuntimeError(f"Unknown TTS provider: {provider}")


async def transcribe_audio(wav_bytes: bytes) -> tuple[str, str]:
    try:
        return await transcribe_with_provider(ASR_PROVIDER, wav_bytes), ASR_PROVIDER
    except Exception as primary_exc:
        if ASR_FALLBACK == "none" or ASR_FALLBACK == ASR_PROVIDER:
            raise
        print(
            f"[ASR] provider={ASR_PROVIDER} failed: "
            f"{type(primary_exc).__name__}: {primary_exc}"
        )
        print(f"[ASR] falling back to {ASR_FALLBACK}")
        return (
            await transcribe_with_provider(ASR_FALLBACK, wav_bytes),
            ASR_FALLBACK,
        )


async def synthesize_speech(text: str) -> tuple[bytes, int, str]:
    try:
        pcm, rate = await synthesize_with_provider(TTS_PROVIDER, text)
        return pcm, rate, TTS_PROVIDER
    except Exception as primary_exc:
        if TTS_FALLBACK == "none" or TTS_FALLBACK == TTS_PROVIDER:
            raise
        print(
            f"[TTS] provider={TTS_PROVIDER} failed: "
            f"{type(primary_exc).__name__}: {primary_exc}"
        )
        print(f"[TTS] falling back to {TTS_FALLBACK}")
        pcm, rate = await synthesize_with_provider(TTS_FALLBACK, text)
        return pcm, rate, TTS_FALLBACK


async def openai_transcribe(wav_bytes: bytes) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = {"file": ("utterance.wav", wav_bytes, "audio/wav")}
    data = {
        "model": OPENAI_TRANSCRIBE_MODEL,
        "language": "zh",
        "prompt": "家庭AI助手语音。可能包含游戏名称、股票、黄金、空调、小米智能家居等词汇。",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await _post_with_retry(
            client,
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            files=files,
            data=data,
        )
        return str(r.json().get("text", "")).strip()


def build_agent_prompt(transcript: str, context: dict[str, Any]) -> str:
    current = context.get("current") or {}
    previous = context.get("previous") or {}
    nxt = context.get("next") or {}
    return f"""你正在作为一个屏幕挂件形态的家庭AI助手与用户语音交流。
回答以自然中文口语为主，适合直接TTS播报。
默认只回答 1～3 句，优先控制在 120 个中文字符左右；用户明确要求详细说明时才展开。
不要复述系统说明，也不要说“根据你提供的上下文”。
不要使用 Markdown 表格、标题符号或长列表，输出就是要直接说给用户听的话。

Glass2 当前资讯上下文：
- 当前：[{current.get('category','')}] {current.get('headline','')} (id={current.get('item_id','')})
  摘要：{current.get('summary','')}
  来源：{current.get('source','')}
- 上一条：[{previous.get('category','')}] {previous.get('headline','')} (id={previous.get('item_id','')})
- 下一条：[{nxt.get('category','')}] {nxt.get('headline','')} (id={nxt.get('item_id','')})

指代规则：
- “这个 / 这条 / 当前这个”默认指当前资讯。
- “上一条 / 刚才那条”优先指上一条资讯。
- “下一条”指下一条资讯。
- 如果用户是在追问某条资讯，例如“这个讲讲 / 详细说说 / 为什么 / 有什么影响 / 后面怎么看”，必须优先调用 HomeAI Info Skill 的 homeai_info.get_item，并使用对应资讯的 item_id 获取完整事实背景后再回答。
- 对 get_item 的固定调用语义是：tool=homeai_info.get_item，protocol=homeai-info/1.1，item_id=当前被指代资讯的 item_id。
- 不要只凭屏幕标题或短摘要推测细节。
- 如果用户不是在询问资讯，就按普通家庭助手请求处理，并可使用 OpenClaw 已配置的其他工具。

用户说：{transcript}
"""


async def openclaw_chat(transcript: str, context: dict[str, Any]) -> str:
    headers = {"Content-Type": "application/json"}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"

    payload = {
        "model": OPENCLAW_MODEL,
        "user": OPENCLAW_USER,
        "stream": False,
        "messages": [
            {"role": "user", "content": build_agent_prompt(transcript, enrich_info_context(context))}
        ],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await _post_with_retry(
            client,
            f"{OPENCLAW_BASE_URL}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        body = r.json()
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenClaw returned no choices: {body}")
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    text = str(content).strip()
    if not text:
        raise RuntimeError("OpenClaw returned empty assistant text")
    if len(text) > MAX_AGENT_CHARS:
        text = text[:MAX_AGENT_CHARS].rstrip() + "。"
    return text


async def openai_tts_wav(text: str) -> bytes:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_TTS_MODEL,
        "voice": OPENAI_TTS_VOICE,
        "input": text[:4096],
        "response_format": "wav",
        "instructions": OPENAI_TTS_INSTRUCTIONS,
        "speed": OPENAI_TTS_SPEED,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await _post_with_retry(
            client,
            "https://api.openai.com/v1/audio/speech",
            headers=headers,
            json=payload,
        )
        return r.content


def _find_quiet_pcm_cut(
    pcm: bytes,
    target: int,
    sample_rate: int,
    *,
    search_ms: int = 1200,
    frame_ms: int = 20,
) -> int:
    """Find a low-energy/near-zero cut around target for PCM16 mono LE."""
    total = len(pcm) - (len(pcm) % 2)
    if target <= 0 or target >= total:
        return max(0, min(total, target - (target % 2)))

    bytes_per_ms = sample_rate * 2 / 1000.0
    radius = max(2, int(search_ms * bytes_per_ms))
    frame_bytes = max(2, int(frame_ms * bytes_per_ms))
    frame_bytes -= frame_bytes % 2

    lo = max(0, target - radius)
    hi = min(total, target + radius)
    lo -= lo % 2
    hi -= hi % 2

    best_frame_start = None
    best_energy = None

    # Score frames by mean absolute amplitude. This strongly prefers pauses.
    for pos in range(lo, max(lo, hi - frame_bytes) + 1, frame_bytes):
        frame = pcm[pos:pos + frame_bytes]
        if len(frame) < 2:
            continue
        samples = memoryview(frame).cast("h")
        if os.sys.byteorder != "little":
            # macOS/ESP deployment is little-endian in practice; keep a safe fallback.
            vals = array.array("h", frame)
            vals.byteswap()
            samples_iter = vals
        else:
            samples_iter = samples
        energy = sum(abs(int(v)) for v in samples_iter) / max(1, len(samples_iter))
        distance_penalty = abs(pos - target) / max(1, radius)
        score = energy * (1.0 + 0.08 * distance_penalty)
        if best_energy is None or score < best_energy:
            best_energy = score
            best_frame_start = pos

    if best_frame_start is None:
        cut = target - (target % 2)
        return max(2, min(total - 2, cut))

    frame_end = min(total, best_frame_start + frame_bytes)
    # Within the quiet frame, choose the sample closest to zero and reasonably
    # near its middle. This minimizes discontinuity between playback segments.
    frame = pcm[best_frame_start:frame_end]
    vals = array.array("h")
    vals.frombytes(frame[: len(frame) - (len(frame) % 2)])
    if os.sys.byteorder != "little":
        vals.byteswap()
    if not vals:
        return best_frame_start

    mid = len(vals) // 2
    window_lo = max(0, mid - len(vals) // 3)
    window_hi = min(len(vals), mid + len(vals) // 3)
    idx = min(
        range(window_lo, max(window_lo + 1, window_hi)),
        key=lambda i: abs(int(vals[i])),
    )
    cut = best_frame_start + idx * 2
    cut -= cut % 2
    return max(2, min(total - 2, cut))


def split_pcm_for_device(
    pcm: bytes,
    sample_rate: int,
    max_bytes: int = DEVICE_TTS_SEGMENT_MAX_BYTES,
) -> list[bytes]:
    """Split PCM into device-safe segments, preferring natural quiet points."""
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    if not pcm:
        return []
    if max_bytes < 32 * 1024:
        raise ValueError("DEVICE_TTS_SEGMENT_MAX_BYTES is unreasonably small")
    max_bytes -= max_bytes % 2

    segments: list[bytes] = []
    pos = 0
    while len(pcm) - pos > max_bytes:
        target = pos + max_bytes
        cut = _find_quiet_pcm_cut(pcm, target, sample_rate)
        # Never let the quiet-point search accidentally create an oversized segment.
        if cut <= pos or cut - pos > max_bytes:
            cut = target
        cut -= cut % 2
        segments.append(pcm[pos:cut])
        pos = cut

    if pos < len(pcm):
        segments.append(pcm[pos:])

    return [seg for seg in segments if seg]


async def _send_pcm_segment(
    session: ClientSession,
    pcm: bytes,
    sample_rate: int,
    *,
    index: int,
    total: int,
) -> None:
    duration_sec = len(pcm) / max(1, sample_rate * 2)

    await send_json(session.ws, {
        "type": "tts.start",
        "sample_rate": sample_rate,
        "format": "pcm_s16le",
        "channels": 1,
        "bytes": len(pcm),
        "segment_index": index,
        "segment_total": total,
    })

    for offset in range(0, len(pcm), TTS_CHUNK_BYTES):
        await session.ws.send(pcm[offset:offset + TTS_CHUNK_BYTES])

    await send_json(session.ws, {
        "type": "tts.end",
        "segment_index": index,
        "segment_total": total,
    })

    print(
        f"[TTS-PIPE] queued-to-device {index}/{total} "
        f"bytes={len(pcm)} duration={duration_sec:.2f}s"
    )


async def _wait_tts_event(
    session: ClientSession,
    wanted: str,
    timeout: float,
) -> None:
    if wanted == "slot":
        event = session.playback_slot_ready_event
    elif wanted == "done":
        event = session.playback_done_event
    else:
        raise ValueError(wanted)

    wanted_task = asyncio.create_task(event.wait())
    error_task = asyncio.create_task(session.playback_error_event.wait())
    try:
        finished, pending = await asyncio.wait(
            {wanted_task, error_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

        if not finished:
            raise RuntimeError(
                f"device TTS pipeline timeout waiting={wanted} "
                f"after {timeout:.1f}s"
            )
        if error_task in finished and error_task.result():
            raise RuntimeError(
                f"device TTS pipeline error while waiting={wanted}"
            )
    finally:
        for task in (wanted_task, error_task):
            if not task.done():
                task.cancel()


async def send_pcm_for_playback(
    session: ClientSession,
    pcm: bytes,
    sample_rate: int,
) -> None:
    segments = split_pcm_for_device(pcm, sample_rate)
    if not segments:
        raise RuntimeError("empty TTS PCM")

    total = len(segments)
    total_duration = len(pcm) / max(1, sample_rate * 2)
    max_segment_duration = max(
        len(seg) / max(1, sample_rate * 2)
        for seg in segments
    )

    print(
        f"[TTS-PIPE] total_bytes={len(pcm)} duration={total_duration:.2f}s "
        f"segments={total} max_segment={DEVICE_TTS_SEGMENT_MAX_BYTES}"
    )

    session.playback_sequence_active = True
    session.playback_done_event.clear()
    session.playback_error_event.clear()
    session.playback_slot_ready_event.clear()

    try:
        await send_state(session.ws, "speaking")

        # Fill both M5Unified speaker queue slots before the first segment can
        # finish. This removes the old "playback.done -> transfer next segment"
        # dead air.
        initial = min(2, total)
        for idx in range(initial):
            await _send_pcm_segment(
                session,
                segments[idx],
                sample_rate,
                index=idx + 1,
                total=total,
            )

        next_index = initial

        # Every 2->1 queue transition on StickS3 releases one PSRAM slot.
        # Refill it immediately while the other segment is still playing.
        while next_index < total:
            timeout = max(
                20.0,
                max_segment_duration + DEVICE_TTS_PLAYBACK_MARGIN_SEC,
            )
            await _wait_tts_event(session, "slot", timeout)
            session.playback_slot_ready_event.clear()

            await _send_pcm_segment(
                session,
                segments[next_index],
                sample_rate,
                index=next_index + 1,
                total=total,
            )
            next_index += 1

        # Final playback.done arrives only after the final queued segment ends.
        final_timeout = max(
            30.0,
            min(
                total_duration + DEVICE_TTS_PLAYBACK_MARGIN_SEC,
                2 * max_segment_duration + DEVICE_TTS_PLAYBACK_MARGIN_SEC,
            ),
        )
        await _wait_tts_event(session, "done", final_timeout)
        print("[TTS-PIPE] gapless sequence complete")

    finally:
        session.playback_sequence_active = False


async def process_utterance(session: ClientSession) -> None:
    if session.processing:
        return
    session.processing = True
    try:
        pcm = bytes(session.audio)
        if len(pcm) < 640:  # < 20 ms
            raise RuntimeError("recording too short")

        wav_bytes = pcm_to_wav_bytes(pcm)
        _best_effort_write_bytes(BASE_DIR / "latest_input.wav", wav_bytes)

        safe_mode = (
            session.diag_glass_mode.lower()
            .replace("glass ", "")
            .replace(" ", "_")
            .replace("/", "_")
        )
        mode_path = BASE_DIR / f"latest_input_{safe_mode}.wav"
        _best_effort_write_bytes(mode_path, wav_bytes)

        print(
            f"[AUDIO] captured {len(pcm)} PCM bytes "
            f"({len(pcm)/(MIC_RATE*2):.2f}s) "
            f"glass={session.diag_glass_mode} saved={mode_path.name}"
        )
        level = pcm_level_stats(pcm)
        print(
            "[LEVEL] raw mic "
            f"peak={level['peak_dbfs']:.1f} dBFS "
            f"rms={level['rms_dbfs']:.1f} dBFS "
            f"noise~={level['noise_dbfs']:.1f} dBFS "
            f"speech~={level['speech_dbfs']:.1f} dBFS "
            f"SNR~={level['snr_db']:.1f} dB "
            f"clip={level['clip_pct']:.3f}%"
        )

        await send_state(session.ws, "thinking")

        if MODE == "loopback":
            # First P0-A2 acceptance test: the user should hear their own voice.
            await send_pcm_for_playback(session, pcm, MIC_RATE)
            session.processing = False
            await send_state(session.ws, "idle")
            return

        if MODE != "full":
            raise RuntimeError(f"Unknown P0_MODE={MODE!r}; use loopback or full")

        turn_started = time.perf_counter()

        asr_started = time.perf_counter()
        transcript, asr_used = await transcribe_audio(wav_bytes)
        asr_ms = int((time.perf_counter() - asr_started) * 1000)
        print(f"[ASR:{asr_used}] {transcript}")
        if not transcript:
            raise RuntimeError("ASR returned empty text")
        _best_effort_write_text(BASE_DIR / "latest_transcript.txt", transcript + "\n")

        await send_json(session.ws, {"type": "asr.result", "text": transcript})

        print(
            f"[STAGE] OpenClaw begin url={OPENCLAW_BASE_URL}/v1/chat/completions "
            f"model={OPENCLAW_MODEL} user={OPENCLAW_USER}"
        )
        agent_started = time.perf_counter()
        answer = await openclaw_chat(transcript, session.context)
        print("[STAGE] OpenClaw returned")
        agent_ms = int((time.perf_counter() - agent_started) * 1000)
        print(f"[AGENT] {answer}")
        _best_effort_write_text(BASE_DIR / "latest_answer.txt", answer + "\n")
        await send_json(session.ws, {"type": "assistant.text", "text": answer})

        print(f"[STAGE] TTS begin provider={TTS_PROVIDER}")
        tts_started = time.perf_counter()
        pcm_out, sample_rate, tts_used = await synthesize_speech(answer)
        print("[STAGE] TTS returned")
        tts_ms = int((time.perf_counter() - tts_started) * 1000)
        _best_effort_write_bytes(
            BASE_DIR / "latest_tts.wav", pcm_to_wav_bytes(pcm_out, sample_rate)
        )

        total_ms = int((time.perf_counter() - turn_started) * 1000)
        print(f"[TTS:{tts_used}] {len(pcm_out)} bytes @ {sample_rate} Hz")
        print(
            f"[LATENCY] asr={asr_ms}ms agent={agent_ms}ms "
            f"tts={tts_ms}ms total_before_playback={total_ms}ms"
        )
        await send_pcm_for_playback(session, pcm_out, sample_rate)
        session.processing = False
        await send_state(session.ws, "idle")

    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        print("[TRACEBACK-BEGIN]")
        traceback.print_exc()
        print("[TRACEBACK-END]")
        try:
            await send_json(session.ws, {"type": "assistant.error", "message": str(exc)[:180]})
            await send_state(session.ws, "error")
            await asyncio.sleep(1.5)
            await send_state(session.ws, "idle")
        except Exception:
            pass
        session.processing = False


async def handle_connection(ws) -> None:
    request = getattr(ws, "request", None)
    path = getattr(request, "path", "") if request is not None else ""
    if path and path.split("?", 1)[0] != WS_PATH:
        await ws.close(code=1008, reason="wrong path")
        return

    session = ClientSession(ws=ws)
    ACTIVE_SESSIONS.append(session)
    print(f"[WS] client connected path={path or WS_PATH}")
    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                if session.recording and len(session.audio) < MAX_INPUT_BYTES:
                    remaining = MAX_INPUT_BYTES - len(session.audio)
                    session.audio.extend(raw[:remaining])
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = msg.get("type")
            if kind == "device.hello":
                print("[DEVICE] hello")
                await send_json(ws, {"type": "gateway.ready", "mode": MODE})
                await send_state(ws, "idle")
                await send_display_policy(session)
                await send_info_sync(session)

            elif kind == "info.ack":
                print(f"[INFO] device ack revision={FEED_REVISION}")

            elif kind == "ptt.start":
                if session.processing:
                    continue
                session.audio.clear()
                session.context = dict(msg.get("context") or {})
                session.diag_glass_mode = str(msg.get("diag_glass_mode") or "GLASS NORMAL")
                session.recording = True
                await send_state(ws, "listening")
                current = (session.context.get("current") or {}).get("headline", "")
                print(f"[PTT] start current={current}")

            elif kind == "ptt.stop":
                session.recording = False
                declared = ((msg.get("audio") or {}).get("bytes"))
                print(f"[PTT] stop received={len(session.audio)} declared={declared}")
                asyncio.create_task(process_utterance(session))

            elif kind == "playback.slot_ready":
                print("[AUDIO] device TTS slot ready")
                if session.playback_sequence_active:
                    session.playback_slot_ready_event.set()

            elif kind == "playback.done":
                print("[AUDIO] device playback done")
                if session.playback_sequence_active:
                    session.playback_done_event.set()
                else:
                    session.processing = False
                    await send_state(ws, "idle")

            elif kind == "playback.error":
                if session.playback_sequence_active:
                    session.playback_error_event.set()
                else:
                    session.processing = False
                    await send_state(ws, "error")

    finally:
        if session in ACTIVE_SESSIONS:
            ACTIVE_SESSIONS.remove(session)
        print("[WS] client disconnected")


async def preflight() -> int:
    print("=== HomeAIAgent P0-A3.8 persistent-config preflight ===")
    print(f"[CFG] persistent_config={PERSISTENT_ENV}")
    print(f"[CFG] persistent_exists={PERSISTENT_ENV.exists()}")
    print(f"[CFG] mode={MODE}")
    print(f"[CFG] ASR={ASR_PROVIDER} fallback={ASR_FALLBACK}")
    print(f"[CFG] TTS={TTS_PROVIDER} fallback={TTS_FALLBACK}")
    print(f"[CFG] Volc ASR endpoint={VOLCENGINE_ASR_ENDPOINT}")
    print(f"[CFG] Volc ASR resource={VOLCENGINE_ASR_RESOURCE_ID}")
    print(
        f"[CFG] Volc ASR chunk={VOLCENGINE_ASR_CHUNK_MS}ms "
        f"send_interval={VOLCENGINE_ASR_SEND_INTERVAL_MS}ms "
        f"second_pass={VOLCENGINE_ASR_ENABLE_NONSTREAM}"
    )
    print(f"[CFG] Volc TTS endpoint={VOLCENGINE_TTS_ENDPOINT}")
    print(f"[CFG] Volc TTS resource={VOLCENGINE_TTS_RESOURCE_ID}")
    print(
        f"[CFG] Volc TTS voice={VOLCENGINE_TTS_VOICE} "
        f"rate={VOLCENGINE_TTS_SAMPLE_RATE}"
    )
    print(f"[CFG] OpenClaw={OPENCLAW_BASE_URL} model={OPENCLAW_MODEL}")
    print(f"[CFG] session user={OPENCLAW_USER}")
    print(f"[CFG] info_skill_protocol={INFO_SKILL_PROTOCOL}")
    print(f"[CFG] info_skill_cache={INFO_SKILL_CACHE_FILE}")
    print(
        f"[CFG] info_skill_feed={INFO_GAME_LIMIT} game + "
        f"{INFO_FINANCE_LIMIT} finance / max {INFO_MAX_ITEMS}"
    )
    print(
        f"[CFG] info_schedule_tz={INFO_SCHEDULE_TIMEZONE} "
        "hours=00,01,09-23"
    )
    print(
        f"[CFG] gold_status=24K spot CNY/g "
        f"refresh={GOLD_REFRESH_SEC}s"
    )
    print(
        "[CFG] screen_protection=01:05-09:00 "
        f"timezone={INFO_SCHEDULE_TIMEZONE}"
    )
    print(f"[CFG] gold_quote_url={GOLD_QUOTE_URL}")

    if MODE != "full":
        print("[WARN] P0_MODE is not 'full'; full AI conversation is disabled.")

    for kind, primary, fallback in (
        ("ASR", ASR_PROVIDER, ASR_FALLBACK),
        ("TTS", TTS_PROVIDER, TTS_FALLBACK),
    ):
        for provider in {primary, fallback} - {"none"}:
            if not _provider_enabled(provider):
                print(f"[FAIL] Unknown {kind} provider={provider}")
                return 2
            if provider == "volcengine" and not _volcengine_has_credentials():
                print(
                    f"[FAIL] {kind} needs Volcengine credentials. "
                    "Set VOLCENGINE_API_KEY from the new Doubao Speech console."
                )
                return 2
            if provider == "openai" and not OPENAI_API_KEY:
                print(f"[FAIL] {kind} provider=openai but OPENAI_API_KEY is empty.")
                return 2

    if (
        ASR_PROVIDER == "volcengine"
        or TTS_PROVIDER == "volcengine"
        or ASR_FALLBACK == "volcengine"
        or TTS_FALLBACK == "volcengine"
    ):
        print("[OK] Volcengine Speech API Key present (X-Api-Key; value not printed).")

    if (
        ASR_PROVIDER == "openai"
        or TTS_PROVIDER == "openai"
        or ASR_FALLBACK == "openai"
        or TTS_FALLBACK == "openai"
    ):
        print("[OK] OpenAI fallback credentials present (value not printed).")

    headers: dict[str, str] = {}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{OPENCLAW_BASE_URL}/v1/models", headers=headers)
            r.raise_for_status()
            body = r.json()
    except Exception as exc:
        print(f"[FAIL] OpenClaw /v1/models: {type(exc).__name__}: {exc}")
        return 3

    model_ids = [
        str(item.get("id", ""))
        for item in (body.get("data") or [])
        if isinstance(item, dict)
    ]
    print(f"[OK] OpenClaw reachable. models={model_ids}")
    if OPENCLAW_MODEL not in model_ids:
        print(f"[WARN] configured OpenClaw model not listed: {OPENCLAW_MODEL}")

    print("[READY] Streaming ASR 2.0 + Doubao TTS configuration is ready.")
    return 0


async def main() -> None:
    load_info_skill_cache()
    load_gold_quote_cache()

    if MODE == "full":
        print(f"[MODE] full: {ASR_PROVIDER} ASR2 streaming -> OpenClaw -> {TTS_PROVIDER} TTS")
        if TTS_PROVIDER == "volcengine":
            print("[TTS] Volcengine V3 unidirectional WebSocket")
    else:
        print("[MODE] loopback: Mic -> Gateway -> StickS3 speaker")
    print(f"[WS] listening on ws://{HOST}:{PORT}{WS_PATH}")
    async with websockets.serve(
        handle_connection,
        HOST,
        PORT,
        max_size=2 * 1024 * 1024,
        ping_interval=None,  # P0: avoid false disconnects during embedded audio work
    ):
        info_task = asyncio.create_task(info_skill_poll_loop())
        gold_task = asyncio.create_task(gold_quote_loop())
        display_task = asyncio.create_task(display_schedule_loop())
        try:
            await asyncio.Future()
        finally:
            info_task.cancel()
            gold_task.cancel()
            display_task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HomeAIAgent A4.1 voice + OpenClaw Info Skill gateway")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate OpenAI/OpenClaw configuration and exit",
    )
    args = parser.parse_args()

    if args.check:
        raise SystemExit(asyncio.run(preflight()))
    asyncio.run(main())
