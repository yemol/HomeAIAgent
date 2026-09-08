#!/usr/bin/env python3
"""HomeAIAgent local Gateway for voice, OpenClaw, Info feed and display policy.

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
import shutil
import array
import struct
import sys
import time
import traceback
import uuid
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
import httpx
import websockets
from websockets.exceptions import ConnectionClosed
from dotenv import load_dotenv

from openclaw_transport import OpenClawTransportConfig, OpenClawTransportManager

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

# Fixed wall-clock schedule.
# Token-saving plan: refresh every two hours at
# 09,11,13,15,17,19,21,23,01, then pause until 09:00.
INFO_SCHEDULE_TIMEZONE = os.getenv("HOMEAI_INFO_TIMEZONE", "Asia/Taipei").strip()
INFO_ALLOWED_HOURS = frozenset([1, 9, 11, 13, 15, 17, 19, 21, 23])

# Each category is an independent machine-data request. Retry only on failure;
# valid empty feeds (0 items) are accepted and are not retried.
INFO_CATEGORY_ORDER = ("game", "finance")
INFO_CATEGORY_RETRY_DELAYS_SEC = (0, 5, 15)

# Screen protection. The Gateway is the wall-clock authority so the
# StickS3 does not need NTP/Internet time of its own.
DISPLAY_SLEEP_HOUR = 1
DISPLAY_SLEEP_MINUTE = 5
DISPLAY_WAKE_HOUR = 9
DISPLAY_WAKE_MINUTE = 0

# Display command handshake.
# "sent" is not considered success. The device must report status=applied
# and an actual sleeping state matching the requested policy.
DISPLAY_ACK_TIMEOUT_SEC = 5.0
DISPLAY_APPLY_TIMEOUT_SEC = 180.0
DISPLAY_COMMAND_MAX_ATTEMPTS = 3
DISPLAY_STATUS_RECHECK_SEC = 300

INFO_FRAME_WIDTH = 128
INFO_FRAME_HEIGHT = 64

INFO_SKILL_CACHE_FILE = HOMEAI_DATA_DIR / "info_skill_feed_cache.json"
INFO_SKILL_BAD_RESPONSE_DIR = HOMEAI_DATA_DIR / "info_skill_bad_responses"
INFO_SKILL_STARTUP_SNAPSHOT_DIR = HOMEAI_DATA_DIR / "info_skill_startup_snapshots"
INFO_SKILL_STARTUP_SNAPSHOT_KEEP = max(1, int(os.getenv("HOMEAI_INFO_STARTUP_SNAPSHOT_KEEP", "20")))
INFO_SKILL_DELIVERY_TOOL = "homeai_info_deliver"

# Bottom status: current 24K spot-equivalent gold value in CNY/gram.
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


@dataclass
class ReminderRecord:
    reminder_id: str
    dedup_key: str
    text: str
    title: str = "提醒"
    job_id: str = ""
    run_id: str = ""
    scheduled_at: str = ""
    fired_at: str = ""
    received_at: str = ""
    attempts: int = 0
    next_attempt_at: float = 0.0
    last_error: str = ""


ACTIVE_SESSIONS: list["ClientSession"] = []
REMINDER_PENDING: list[ReminderRecord] = []
REMINDER_DELIVERED: list[dict[str, str]] = []
REMINDER_DELIVERED_KEYS: set[str] = set()
REMINDER_LOCK: asyncio.Lock | None = None
FEED_ITEMS: list[FeedItem] = []
FEED_ITEM_HISTORY: dict[str, FeedItem] = {}
FEED_REVISION = "boot"

# Keep independent last-good state for game and finance so one broken
# category never freezes the other category.
FEED_CATEGORY_ITEMS: dict[str, list[FeedItem]] = {
    "game": [],
    "finance": [],
}
FEED_CATEGORY_UPDATED_AT: dict[str, str] = {
    "game": "",
    "finance": "",
}

# Refresh diagnostics. Active during each real Info refresh:
# Gateway startup freshness barrier, never during normal wall-clock polling.
ACTIVE_INFO_STARTUP_SNAPSHOT_DIR: Path | None = None
LAST_INFO_REFRESH_DIAGNOSTIC: dict[str, Any] = {}


HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
PORT = int(os.getenv("GATEWAY_PORT", "8765"))
WS_PATH = os.getenv("GATEWAY_PATH", "/companion")
MODE = os.getenv("P0_MODE", "full").strip().lower()

# HomeAIAgent Notification A1. OpenClaw background reminders already land in
# the same stable voice session used by /v1/chat/completions. Instead of opening
# a reverse webhook port, the Mac mini keeps one outbound Gateway WebSocket over
# the existing 127.0.0.1:18790 SSH/Tailscale tunnel and subscribes to that exact
# session. The authoritative transcript is reconciled through chat.history.
NOTIFICATION_LISTENER_ENABLED = (
    os.getenv("HOMEAI_NOTIFICATION_LISTENER_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
NOTIFICATION_HISTORY_LIMIT = max(
    10,
    min(200, int(os.getenv("HOMEAI_NOTIFICATION_HISTORY_LIMIT", "60"))),
)
NOTIFICATION_RECONNECT_MIN_SEC = max(
    1.0, float(os.getenv("HOMEAI_NOTIFICATION_RECONNECT_MIN_SEC", "2"))
)
NOTIFICATION_RECONNECT_MAX_SEC = max(
    NOTIFICATION_RECONNECT_MIN_SEC,
    float(os.getenv("HOMEAI_NOTIFICATION_RECONNECT_MAX_SEC", "30")),
)
NOTIFICATION_SEEN_HISTORY = max(
    100, int(os.getenv("HOMEAI_NOTIFICATION_SEEN_HISTORY", "500"))
)
NOTIFICATION_STATE_FILE = HOMEAI_DATA_DIR / "openclaw_voice_listener_state.json"
REMINDER_QUEUE_FILE = HOMEAI_DATA_DIR / "notification_queue.json"
REMINDER_RETRY_MAX_SEC = max(30, int(os.getenv("HOMEAI_REMINDER_RETRY_MAX_SEC", "300")))
REMINDER_DELIVERED_HISTORY = max(20, int(os.getenv("HOMEAI_REMINDER_DELIVERED_HISTORY", "200")))

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

A1R7_STANDARD_TTS_RESOURCE_ID = "seed-tts-2.0"
A1R7_STANDARD_TTS_VOICE = "zh_female_vv_uranus_bigtts"


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


def _iso_now() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat()


def _normalize_reminder_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    text = " ".join(str(value).strip().split())
    # Async assistant output should remain complete. Protect the TTS path from
    # pathological transcript rows without hard-cutting a normal reminder.
    if len(text) > 1200:
        return ""
    return text


def _normalize_notification_compare_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _notification_text_digest(value: str) -> str:
    normalized = _normalize_notification_compare_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _history_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _history_message_identity(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    meta = message.get("__openclaw")
    if not isinstance(meta, dict):
        meta = {}
    entry_id = str(meta.get("id") or message.get("id") or "").strip()
    seq = meta.get("seq")
    text = _history_message_text(message)
    role = str(message.get("role") or "").strip().lower()
    digest = _notification_text_digest(text)[:20] if text else "empty"
    if entry_id:
        return f"id:{entry_id}:seq:{seq if seq is not None else '-'}:{role}:{digest}"
    timestamp = str(message.get("timestamp") or message.get("createdAt") or "").strip()
    return f"fallback:{timestamp}:{role}:{digest}"


def _is_user_visible_async_assistant_message(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    if str(message.get("role") or "").strip().lower() != "assistant":
        return False
    meta = message.get("__openclaw")
    if isinstance(meta, dict):
        kind = str(meta.get("kind") or "").strip().lower()
        if kind in {"compaction", "tool", "tool_result", "system"}:
            return False
    text = _normalize_reminder_text(_history_message_text(message))
    if not text:
        return False
    if text.startswith("[chat.history omitted:"):
        return False
    return True


NOTIFICATION_SEEN_KEYS: list[str] = []
NOTIFICATION_SEEN_SET: set[str] = set()
NOTIFICATION_CURSOR_INITIALIZED = False
NOTIFICATION_CURSOR_SESSION_KEY = ""
NOTIFICATION_CURSOR_SESSION_ID = ""
VOICE_OPENCLAW_INFLIGHT = 0
VOICE_REPLY_SUPPRESSIONS: list[dict[str, Any]] = []


def _remember_notification_seen(key: str) -> None:
    if not key or key in NOTIFICATION_SEEN_SET:
        return
    NOTIFICATION_SEEN_KEYS.append(key)
    NOTIFICATION_SEEN_SET.add(key)
    while len(NOTIFICATION_SEEN_KEYS) > NOTIFICATION_SEEN_HISTORY:
        old = NOTIFICATION_SEEN_KEYS.pop(0)
        NOTIFICATION_SEEN_SET.discard(old)


def load_notification_listener_state(session_key: str) -> None:
    global NOTIFICATION_CURSOR_INITIALIZED
    global NOTIFICATION_CURSOR_SESSION_KEY
    global NOTIFICATION_CURSOR_SESSION_ID

    NOTIFICATION_SEEN_KEYS.clear()
    NOTIFICATION_SEEN_SET.clear()
    NOTIFICATION_CURSOR_INITIALIZED = False
    NOTIFICATION_CURSOR_SESSION_KEY = session_key
    NOTIFICATION_CURSOR_SESSION_ID = ""

    if not NOTIFICATION_STATE_FILE.exists():
        return
    try:
        body = json.loads(NOTIFICATION_STATE_FILE.read_text(encoding="utf-8"))
        if str(body.get("session_key") or "") != session_key:
            print("[NOTIFY] listener state belongs to another session; new baseline required")
            return
        for raw in body.get("seen_message_keys") or []:
            key = str(raw or "").strip()
            if key:
                _remember_notification_seen(key)
        NOTIFICATION_CURSOR_INITIALIZED = bool(body.get("initialized", False))
        NOTIFICATION_CURSOR_SESSION_ID = str(body.get("session_id") or "")
        print(
            f"[NOTIFY] listener state loaded initialized={NOTIFICATION_CURSOR_INITIALIZED} "
            f"seen={len(NOTIFICATION_SEEN_KEYS)}"
        )
    except Exception as exc:
        print(
            f"[NOTIFY-WARN] listener state read failed; new baseline required: "
            f"{type(exc).__name__}: {exc}"
        )


def save_notification_listener_state() -> bool:
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        body = {
            "version": 1,
            "saved_at": _iso_now(),
            "session_key": NOTIFICATION_CURSOR_SESSION_KEY,
            "session_id": NOTIFICATION_CURSOR_SESSION_ID,
            "initialized": NOTIFICATION_CURSOR_INITIALIZED,
            "seen_message_keys": NOTIFICATION_SEEN_KEYS[-NOTIFICATION_SEEN_HISTORY:],
        }
        tmp = NOTIFICATION_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(NOTIFICATION_STATE_FILE)
        return True
    except OSError as exc:
        print(f"[NOTIFY-WARN] listener state write failed: {exc}")
        return False


def _register_voice_reply_suppression(text: str) -> None:
    normalized = _normalize_notification_compare_text(text)
    if not normalized:
        return
    now = time.time()
    VOICE_REPLY_SUPPRESSIONS[:] = [
        item for item in VOICE_REPLY_SUPPRESSIONS
        if float(item.get("expires_at") or 0.0) > now
    ]
    VOICE_REPLY_SUPPRESSIONS.append({
        "digest": _notification_text_digest(normalized),
        "expires_at": now + 600.0,
    })
    del VOICE_REPLY_SUPPRESSIONS[:-24]


def _consume_voice_reply_suppression(text: str) -> bool:
    normalized = _normalize_notification_compare_text(text)
    if not normalized:
        return False
    digest = _notification_text_digest(normalized)
    now = time.time()
    kept: list[dict[str, Any]] = []
    consumed = False
    for item in VOICE_REPLY_SUPPRESSIONS:
        if float(item.get("expires_at") or 0.0) <= now:
            continue
        if not consumed and str(item.get("digest") or "") == digest:
            consumed = True
            continue
        kept.append(item)
    VOICE_REPLY_SUPPRESSIONS[:] = kept
    return consumed


def _notification_record_from_history_message(
    session_key: str,
    message: dict[str, Any],
) -> ReminderRecord | None:
    text = _normalize_reminder_text(_history_message_text(message))
    if not text:
        return None
    message_key = _history_message_identity(message)
    if not message_key:
        return None
    dedup_key = f"session:{session_key}:{message_key}"
    reminder_id = "rem-" + hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()[:20]
    return ReminderRecord(
        reminder_id=reminder_id,
        dedup_key=dedup_key,
        text=text,
        title="提醒",
        fired_at=_iso_now(),
        received_at=_iso_now(),
    )


def load_reminder_queue() -> None:
    REMINDER_PENDING.clear()
    REMINDER_DELIVERED.clear()
    REMINDER_DELIVERED_KEYS.clear()

    if not REMINDER_QUEUE_FILE.exists():
        return

    try:
        body = json.loads(REMINDER_QUEUE_FILE.read_text(encoding="utf-8"))
        for raw in body.get("pending") or []:
            if not isinstance(raw, dict):
                continue
            try:
                record = ReminderRecord(
                    reminder_id=str(raw.get("reminder_id") or ""),
                    dedup_key=str(raw.get("dedup_key") or ""),
                    text=str(raw.get("text") or ""),
                    title=str(raw.get("title") or "提醒"),
                    job_id=str(raw.get("job_id") or ""),
                    run_id=str(raw.get("run_id") or ""),
                    scheduled_at=str(raw.get("scheduled_at") or ""),
                    fired_at=str(raw.get("fired_at") or ""),
                    received_at=str(raw.get("received_at") or ""),
                    attempts=max(0, int(raw.get("attempts") or 0)),
                    next_attempt_at=max(0.0, float(raw.get("next_attempt_at") or 0.0)),
                    last_error=str(raw.get("last_error") or ""),
                )
            except Exception:
                continue
            if record.reminder_id and record.dedup_key and record.text:
                REMINDER_PENDING.append(record)

        for raw in (body.get("delivered") or [])[-REMINDER_DELIVERED_HISTORY:]:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("dedup_key") or "")
            if not key:
                continue
            item = {
                "dedup_key": key,
                "reminder_id": str(raw.get("reminder_id") or ""),
                "delivered_at": str(raw.get("delivered_at") or ""),
            }
            REMINDER_DELIVERED.append(item)
            REMINDER_DELIVERED_KEYS.add(key)

        print(
            f"[NOTIFY] queue loaded pending={len(REMINDER_PENDING)} "
            f"delivered_history={len(REMINDER_DELIVERED)}"
        )
    except Exception as exc:
        print(
            f"[NOTIFY-WARN] queue read failed; starting empty: "
            f"{type(exc).__name__}: {exc}"
        )


def save_reminder_queue() -> bool:
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        body = {
            "version": 2,
            "saved_at": _iso_now(),
            "pending": [asdict(item) for item in REMINDER_PENDING],
            "delivered": REMINDER_DELIVERED[-REMINDER_DELIVERED_HISTORY:],
        }
        tmp = REMINDER_QUEUE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(body, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(REMINDER_QUEUE_FILE)
        return True
    except OSError as exc:
        print(f"[NOTIFY-WARN] queue write failed: {exc}")
        return False


OPENCLAW_BASE_URL = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18790").rstrip("/")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")
OPENCLAW_MODEL = os.getenv("OPENCLAW_MODEL", "openclaw/default")
OPENCLAW_USER = os.getenv("OPENCLAW_USER", "home-ai-agent:main")
OPENCLAW_VOICE_AGENT_ID = os.getenv("OPENCLAW_VOICE_AGENT_ID", "main").strip() or "main"

# A1R11: OpenClaw transport is owned by this Python service. On the Mac mini
# the default is an in-process AsyncSSH local forward. A future deployment on
# the OpenClaw host can switch OPENCLAW_TRANSPORT=direct without changing the
# StickS3 protocol.
OPENCLAW_TRANSPORT = os.getenv("OPENCLAW_TRANSPORT", "embedded_ssh").strip().lower()
OPENCLAW_SSH_USER = os.getenv("OPENCLAW_SSH_USER", "yuanxiang").strip() or "yuanxiang"
OPENCLAW_SSH_HOST = os.getenv("OPENCLAW_SSH_HOST", "100.105.66.46").strip()
OPENCLAW_LOCAL_PORT = int(os.getenv("OPENCLAW_LOCAL_PORT", "18790"))
OPENCLAW_REMOTE_PORT = int(os.getenv("OPENCLAW_REMOTE_PORT", "18789"))
OPENCLAW_SSH_CONNECT_TIMEOUT_SEC = max(2.0, float(os.getenv("OPENCLAW_SSH_CONNECT_TIMEOUT_SEC", "10")))
OPENCLAW_SSH_RECONNECT_MIN_SEC = max(0.5, float(os.getenv("OPENCLAW_SSH_RECONNECT_MIN_SEC", "2")))
OPENCLAW_SSH_RECONNECT_MAX_SEC = max(OPENCLAW_SSH_RECONNECT_MIN_SEC, float(os.getenv("OPENCLAW_SSH_RECONNECT_MAX_SEC", "30")))
OPENCLAW_SSH_STARTUP_WAIT_SEC = max(1.0, float(os.getenv("OPENCLAW_SSH_STARTUP_WAIT_SEC", "15")))
OPENCLAW_TRANSPORT_MANAGER: OpenClawTransportManager | None = None


def _openclaw_transport_config() -> OpenClawTransportConfig:
    return OpenClawTransportConfig(
        mode=OPENCLAW_TRANSPORT,
        ssh_user=OPENCLAW_SSH_USER,
        ssh_host=OPENCLAW_SSH_HOST,
        local_port=OPENCLAW_LOCAL_PORT,
        remote_port=OPENCLAW_REMOTE_PORT,
        connect_timeout_sec=OPENCLAW_SSH_CONNECT_TIMEOUT_SEC,
        reconnect_min_sec=OPENCLAW_SSH_RECONNECT_MIN_SEC,
        reconnect_max_sec=OPENCLAW_SSH_RECONNECT_MAX_SEC,
    )


async def _wait_for_openclaw_transport(timeout: float | None = None) -> bool:
    manager = OPENCLAW_TRANSPORT_MANAGER
    if manager is None or OPENCLAW_TRANSPORT == "direct":
        return True
    return await manager.wait_ready(timeout)


def _openclaw_transport_ready() -> bool:
    manager = OPENCLAW_TRANSPORT_MANAGER
    if manager is None or OPENCLAW_TRANSPORT == "direct":
        return True
    return manager.ready.is_set()


def _openclaw_voice_session_key() -> str:
    # Mirrors OpenClaw's current OpenAI-compatible resolver exactly:
    # prefix="openai" + stable `user` -> agent:<agentId>:openai-user:<user>.
    return f"agent:{OPENCLAW_VOICE_AGENT_ID}:openai-user:{OPENCLAW_USER}"


# Info Skill session lifecycle. Each background request gets one exact,
# unique OpenClaw session key so it never shares history with another refresh.
# Cleanup is performed through the OpenClaw Gateway WebSocket RPC itself, not
# SSH or a host-local CLI. This keeps HomeAIAgent independent from whichever
# machine currently hosts OpenClaw.
OPENCLAW_INFO_AGENT_ID = os.getenv("OPENCLAW_INFO_AGENT_ID", "main").strip() or "main"
OPENCLAW_INFO_SESSION_CLEANUP = (
    os.getenv("OPENCLAW_INFO_SESSION_CLEANUP", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
OPENCLAW_INFO_CLEANUP_TIMEOUT_SEC = max(5.0, float(
    os.getenv("OPENCLAW_INFO_CLEANUP_TIMEOUT_SEC", "20")
))
OPENCLAW_GATEWAY_WS_URL = os.getenv("OPENCLAW_GATEWAY_WS_URL", "").strip()
OPENCLAW_GATEWAY_PROTOCOL = int(os.getenv("OPENCLAW_GATEWAY_PROTOCOL", "4"))

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
        "category_updated_at": dict(FEED_CATEGORY_UPDATED_AT),
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

    FEED_CATEGORY_ITEMS["game"] = [
        item for item in FEED_ITEMS if item.category == "游戏"
    ][:INFO_GAME_LIMIT]
    FEED_CATEGORY_ITEMS["finance"] = [
        item for item in FEED_ITEMS if item.category == "金融"
    ][:INFO_FINANCE_LIMIT]

    cached_times = body.get("category_updated_at")
    if isinstance(cached_times, dict):
        for category in INFO_CATEGORY_ORDER:
            FEED_CATEGORY_UPDATED_AT[category] = str(
                cached_times.get(category) or ""
            )[:64]

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


def _info_delivery_tool_spec() -> dict[str, Any]:
    # Use the OpenAI-compatible client function-call channel as a
    # structured return envelope. The internal HomeAI Info Skill still owns
    # acquisition; this tool only transports its result back to the Mini.
    return {
        "type": "function",
        "function": {
            "name": INFO_SKILL_DELIVERY_TOOL,
            "description": (
                "After calling the internal homeai_info.get_feed Skill, "
                "return that Skill result exactly as this function's arguments. "
                "Do not summarize, rewrite, or invent fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protocol_version": {"type": "string"},
                    "protocol": {"type": "string"},
                    "operation": {"type": "string"},
                    "request_id": {"type": "string"},
                    "category": {"type": "string"},
                    "status": {"type": "string"},
                    "next_refresh_after_sec": {"type": "integer"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "item_id": {"type": "string"},
                                "category": {"type": "string"},
                                "headline": {"type": "string"},
                                "summary": {"type": "string"},
                                "source_name": {"type": "string"},
                                "source": {"type": "string"},
                                "source_url": {"type": "string"},
                                "priority": {"type": "integer"},
                                "published_at": {"type": "string"},
                                "content_hash": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "warnings": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "error": {"type": "object"},
                },
                "additionalProperties": True,
            },
        },
    }


def _startup_snapshot_path(name: str) -> Path | None:
    root = ACTIVE_INFO_STARTUP_SNAPSHOT_DIR
    if root is None:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    return root / safe


def _write_startup_snapshot_text(name: str, text: str) -> Path | None:
    path = _startup_snapshot_path(name)
    if path is None:
        return None
    try:
        path.write_text(text, encoding="utf-8")
        return path
    except Exception as exc:
        print(
            f"[INFO-SNAPSHOT-WARN] failed to save {name}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def _write_startup_snapshot_json(name: str, value: Any) -> Path | None:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except Exception as exc:
        print(
            f"[INFO-SNAPSHOT-WARN] failed to serialize {name}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None
    return _write_startup_snapshot_text(name, text)


def _prune_startup_info_snapshots() -> None:
    try:
        INFO_SKILL_STARTUP_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        dirs = sorted(
            [p for p in INFO_SKILL_STARTUP_SNAPSHOT_DIR.iterdir() if p.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        for old in dirs[INFO_SKILL_STARTUP_SNAPSHOT_KEEP:]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception as exc:
        print(
            f"[INFO-SNAPSHOT-WARN] retention cleanup failed: "
            f"{type(exc).__name__}: {exc}"
        )


def _begin_startup_info_snapshot(
    started_at: datetime,
    *,
    trigger: str = "startup",
    slot_label: str = "",
) -> Path | None:
    # Backward-compatible path/function name; records
    # every real Info refresh (startup + fixed 2h scheduled slots).
    global ACTIVE_INFO_STARTUP_SNAPSHOT_DIR
    try:
        INFO_SKILL_STARTUP_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = started_at.strftime("%Y%m%d-%H%M%S-%f")
        safe_trigger = "".join(
            ch if ch.isalnum() or ch in "._-" else "_"
            for ch in str(trigger or "refresh")
        )
        if safe_trigger != "startup":
            stamp = f"{stamp}_{safe_trigger}"
        root = INFO_SKILL_STARTUP_SNAPSHOT_DIR / stamp
        root.mkdir(parents=True, exist_ok=False)
        ACTIVE_INFO_STARTUP_SNAPSHOT_DIR = root
        _write_startup_snapshot_json(
            "manifest.json",
            {
                "kind": "gateway-info-refresh",
                "trigger": trigger,
                "slot": slot_label or None,
                "started_at": started_at.isoformat(),
                "protocol": INFO_SKILL_PROTOCOL,
                "status": "running",
            },
        )
        _prune_startup_info_snapshots()
        print(
            f"[INFO-SNAPSHOT] capture={root} "
            f"trigger={trigger} slot={slot_label or '-'}"
        )
        return root
    except Exception as exc:
        ACTIVE_INFO_STARTUP_SNAPSHOT_DIR = None
        print(
            f"[INFO-SNAPSHOT-WARN] capture unavailable trigger={trigger}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def _finish_startup_info_snapshot(
    *,
    started_at: datetime,
    status: str,
    error: str = "",
    trigger: str = "startup",
    slot_label: str = "",
) -> None:
    global ACTIVE_INFO_STARTUP_SNAPSHOT_DIR
    root = ACTIVE_INFO_STARTUP_SNAPSHOT_DIR
    if root is None:
        return
    try:
        merged = [asdict(item) for item in FEED_ITEMS]
        _write_startup_snapshot_json("final_feed_20.json", merged)
        _write_startup_snapshot_json(
            "manifest.json",
            {
                "kind": "gateway-info-refresh",
                "trigger": trigger,
                "slot": slot_label or None,
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(_info_schedule_tz()).isoformat(),
                "protocol": INFO_SKILL_PROTOCOL,
                "status": status,
                "error": error or None,
                "revision": FEED_REVISION,
                "count": len(FEED_ITEMS),
                "category_counts": {
                    c: len(FEED_CATEGORY_ITEMS.get(c, []))
                    for c in INFO_CATEGORY_ORDER
                },
                "category_updated_at": dict(FEED_CATEGORY_UPDATED_AT),
                "refresh": dict(LAST_INFO_REFRESH_DIAGNOSTIC),
            },
        )
        print(
            f"[INFO-SNAPSHOT] capture complete={root} "
            f"trigger={trigger} slot={slot_label or '-'} "
            f"status={status} revision={FEED_REVISION}"
        )
    finally:
        ACTIVE_INFO_STARTUP_SNAPSHOT_DIR = None


def _save_bad_info_response(
    *,
    category: str,
    stage: str,
    raw: str,
    exc: Exception,
) -> Path | None:
    try:
        INFO_SKILL_BAD_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(_info_schedule_tz()).strftime("%Y%m%d-%H%M%S-%f")
        path = INFO_SKILL_BAD_RESPONSE_DIR / (
            f"{stamp}_{category}_{stage}.txt"
        )
        payload = (
            f"category={category}\n"
            f"stage={stage}\n"
            f"error={type(exc).__name__}: {exc}\n"
            "--- raw response ---\n"
            f"{raw}\n"
        )
        path.write_text(payload, encoding="utf-8")
        return path
    except Exception as save_exc:
        print(
            f"[INFO-SKILL-JSON-WARN] failed to save bad response: "
            f"{type(save_exc).__name__}: {save_exc}"
        )
        return None


def _log_json_decode_context(
    *,
    category: str,
    stage: str,
    raw: str,
    exc: json.JSONDecodeError,
) -> None:
    start = max(0, exc.pos - 120)
    end = min(len(raw), exc.pos + 120)
    near = raw[start:end]
    print(
        f"[INFO-SKILL-JSON] category={category} stage={stage} "
        f"pos={exc.pos} line={exc.lineno} col={exc.colno} "
        f"near={near!r}"
    )
    saved = _save_bad_info_response(
        category=category,
        stage=stage,
        raw=raw,
        exc=exc,
    )
    if saved is not None:
        print(f"[INFO-SKILL-JSON] raw saved={saved}")


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    return str(content or "").strip()


def _new_info_session_key(category: str) -> str:
    safe_category = "".join(
        ch if ch.isalnum() or ch in "_-" else "-"
        for ch in str(category or "info").strip().lower()
    ).strip("-") or "info"
    # Keep a narrow, unmistakable namespace so cleanup can reject anything else.
    return (
        f"agent:{OPENCLAW_INFO_AGENT_ID}:"
        f"homeai-info-{safe_category}-{uuid.uuid4().hex}"
    )


def _is_safe_info_session_key(session_key: str) -> bool:
    prefix = f"agent:{OPENCLAW_INFO_AGENT_ID}:homeai-info-"
    return bool(
        session_key.startswith(prefix)
        and len(session_key) > len(prefix) + 32
        and "home-ai-agent:main" not in session_key
        and session_key != f"agent:{OPENCLAW_INFO_AGENT_ID}:main"
    )


def _openclaw_gateway_ws_url() -> str:
    if OPENCLAW_GATEWAY_WS_URL:
        return OPENCLAW_GATEWAY_WS_URL
    parsed = urlsplit(OPENCLAW_BASE_URL)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise RuntimeError(f"invalid OPENCLAW_BASE_URL for Gateway RPC: {OPENCLAW_BASE_URL!r}")
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    return urlunsplit((scheme, parsed.netloc, "/", "", ""))


async def _gateway_rpc_recv_response(
    ws: Any,
    request_id: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            continue
        if frame.get("type") == "res" and str(frame.get("id", "")) == request_id:
            return frame


async def _openclaw_gateway_rpc(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    ws_url = _openclaw_gateway_ws_url()
    async with websockets.connect(
        ws_url,
        open_timeout=min(timeout, 8.0),
        close_timeout=3,
        max_size=1024 * 1024,
    ) as ws:
        # Current OpenClaw Gateway sends a connect.challenge before the client
        # may authenticate. Ignore unrelated early events but require a valid
        # challenge before sending connect.
        challenge_deadline = asyncio.get_running_loop().time() + min(timeout, 8.0)
        challenge: dict[str, Any] | None = None
        while challenge is None:
            remaining = challenge_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("OpenClaw Gateway connect.challenge timeout")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            frame = json.loads(raw)
            if (
                isinstance(frame, dict)
                and frame.get("type") == "event"
                and frame.get("event") == "connect.challenge"
                and isinstance(frame.get("payload"), dict)
            ):
                challenge = frame["payload"]

        nonce = str(challenge.get("nonce", "") or "")
        ts = challenge.get("ts")
        if not nonce or not isinstance(ts, int) or ts < 0:
            raise RuntimeError("invalid OpenClaw Gateway connect.challenge")

        connect_id = uuid.uuid4().hex
        connect_params: dict[str, Any] = {
            "minProtocol": OPENCLAW_GATEWAY_PROTOCOL,
            "maxProtocol": OPENCLAW_GATEWAY_PROTOCOL,
            "client": {
                "id": "gateway-client",
                "displayName": "HomeAIAgent Info Cleanup",
                "version": "A4.4.18-RC1R13",
                "platform": sys.platform,
                "mode": "backend",
            },
            "role": "operator",
            "scopes": ["operator.admin"],
            "caps": [],
            "commands": [],
            "permissions": {},
        }
        if OPENCLAW_TOKEN:
            connect_params["auth"] = {"token": OPENCLAW_TOKEN}

        await ws.send(json.dumps({
            "type": "req",
            "id": connect_id,
            "method": "connect",
            "params": connect_params,
        }, ensure_ascii=False))
        connect_res = await _gateway_rpc_recv_response(
            ws, connect_id, timeout=min(timeout, 8.0)
        )
        if not bool(connect_res.get("ok")):
            raise RuntimeError(
                f"OpenClaw Gateway connect failed: {connect_res.get('error')!r}"
            )

        request_id = uuid.uuid4().hex
        await ws.send(json.dumps({
            "type": "req",
            "id": request_id,
            "method": method,
            "params": params,
        }, ensure_ascii=False))
        response = await _gateway_rpc_recv_response(
            ws, request_id, timeout=timeout
        )
        if not bool(response.get("ok")):
            raise RuntimeError(
                f"OpenClaw Gateway RPC {method} failed: {response.get('error')!r}"
            )
        payload = response.get("payload")
        return payload if isinstance(payload, dict) else {"payload": payload}


async def _openclaw_listener_connect(*, timeout: float = 15.0) -> Any:
    ws_url = _openclaw_gateway_ws_url()
    ws = await websockets.connect(
        ws_url,
        open_timeout=min(timeout, 8.0),
        close_timeout=3,
        max_size=2 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=20,
    )
    try:
        challenge_deadline = asyncio.get_running_loop().time() + min(timeout, 8.0)
        challenge: dict[str, Any] | None = None
        while challenge is None:
            remaining = challenge_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("OpenClaw Gateway listener connect.challenge timeout")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            frame = json.loads(raw)
            if (
                isinstance(frame, dict)
                and frame.get("type") == "event"
                and frame.get("event") == "connect.challenge"
                and isinstance(frame.get("payload"), dict)
            ):
                challenge = frame["payload"]

        nonce = str(challenge.get("nonce", "") or "")
        ts = challenge.get("ts")
        if not nonce or not isinstance(ts, int) or ts < 0:
            raise RuntimeError("invalid OpenClaw Gateway listener challenge")

        connect_id = uuid.uuid4().hex
        connect_params: dict[str, Any] = {
            "minProtocol": OPENCLAW_GATEWAY_PROTOCOL,
            "maxProtocol": OPENCLAW_GATEWAY_PROTOCOL,
            "client": {
                "id": "gateway-client",
                "displayName": "HomeAIAgent Session Listener",
                "version": "A4.6-Notification-A1",
                "platform": sys.platform,
                "mode": "backend",
            },
            "role": "operator",
            "scopes": ["operator.read"],
            "caps": [],
            "commands": [],
            "permissions": {},
        }
        if OPENCLAW_TOKEN:
            connect_params["auth"] = {"token": OPENCLAW_TOKEN}

        await ws.send(json.dumps({
            "type": "req",
            "id": connect_id,
            "method": "connect",
            "params": connect_params,
        }, ensure_ascii=False))
        connect_res = await _gateway_rpc_recv_response(
            ws, connect_id, timeout=min(timeout, 8.0)
        )
        if not bool(connect_res.get("ok")):
            raise RuntimeError(
                f"OpenClaw Gateway listener connect failed: {connect_res.get('error')!r}"
            )
        return ws
    except Exception:
        await ws.close()
        raise


def _listener_event_targets_voice_session(frame: dict[str, Any], session_key: str) -> bool:
    if frame.get("type") != "event":
        return False
    event_name = str(frame.get("event") or "")
    if event_name not in {"session.message", "sessions.changed"}:
        return False
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return False
    target = payload.get("target")
    target_key = target.get("sessionKey") if isinstance(target, dict) else ""
    candidate = str(
        payload.get("sessionKey")
        or payload.get("key")
        or target_key
        or ""
    )
    return candidate == session_key


async def _listener_rpc(
    ws: Any,
    method: str,
    params: dict[str, Any],
    *,
    session_key: str,
    timeout: float = 15.0,
) -> tuple[dict[str, Any], bool]:
    request_id = uuid.uuid4().hex
    await ws.send(json.dumps({
        "type": "req",
        "id": request_id,
        "method": method,
        "params": params,
    }, ensure_ascii=False))

    dirty = False
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            continue
        if _listener_event_targets_voice_session(frame, session_key):
            dirty = True
        if frame.get("type") == "res" and str(frame.get("id") or "") == request_id:
            if not bool(frame.get("ok")):
                raise RuntimeError(
                    f"OpenClaw Gateway listener RPC {method} failed: {frame.get('error')!r}"
                )
            payload = frame.get("payload")
            return (payload if isinstance(payload, dict) else {"payload": payload}, dirty)


async def _enqueue_async_notification(
    session_key: str,
    message: dict[str, Any],
) -> bool:
    record = _notification_record_from_history_message(session_key, message)
    if record is None:
        return True

    assert REMINDER_LOCK is not None
    async with REMINDER_LOCK:
        duplicate = (
            record.dedup_key in REMINDER_DELIVERED_KEYS
            or any(item.dedup_key == record.dedup_key for item in REMINDER_PENDING)
        )
        if duplicate:
            print(f"[NOTIFY] duplicate transcript message ignored id={record.reminder_id}")
            return True
        REMINDER_PENDING.append(record)
        if not save_reminder_queue():
            REMINDER_PENDING[:] = [
                item for item in REMINDER_PENDING
                if item.dedup_key != record.dedup_key
            ]
            return False

    print(
        f"[NOTIFY] async assistant queued id={record.reminder_id} "
        f"text={record.text!r}"
    )
    return True


async def _reconcile_voice_session_history(
    history: dict[str, Any],
    session_key: str,
) -> None:
    global NOTIFICATION_CURSOR_INITIALIZED
    global NOTIFICATION_CURSOR_SESSION_KEY
    global NOTIFICATION_CURSOR_SESSION_ID

    messages = history.get("messages")
    if not isinstance(messages, list):
        messages = []
    session_id = str(history.get("sessionId") or history.get("session_id") or "")
    NOTIFICATION_CURSOR_SESSION_KEY = session_key
    if session_id:
        NOTIFICATION_CURSOR_SESSION_ID = session_id

    assistant_rows = [
        item for item in messages
        if _is_user_visible_async_assistant_message(item)
    ]

    if not NOTIFICATION_CURSOR_INITIALIZED:
        for message in assistant_rows:
            _remember_notification_seen(_history_message_identity(message))
        NOTIFICATION_CURSOR_INITIALIZED = True
        save_notification_listener_state()
        print(
            f"[NOTIFY] baseline established session={session_key} "
            f"assistant_rows={len(assistant_rows)}; old history will not be spoken"
        )
        return

    # Never classify the synchronous HTTP response as an async notification.
    # The event can arrive before /v1/chat/completions returns, so wait until the
    # current voice request has registered its exact response suppression hash.
    if VOICE_OPENCLAW_INFLIGHT > 0:
        return

    changed = False
    for raw in assistant_rows:
        if not isinstance(raw, dict):
            continue
        key = _history_message_identity(raw)
        if not key or key in NOTIFICATION_SEEN_SET:
            continue
        text = _history_message_text(raw)
        if _consume_voice_reply_suppression(text):
            _remember_notification_seen(key)
            changed = True
            print("[NOTIFY] synchronous voice reply observed and suppressed")
            continue

        queued = await _enqueue_async_notification(session_key, raw)
        if not queued:
            print("[NOTIFY-WARN] queue persistence failed; transcript cursor not advanced")
            break
        _remember_notification_seen(key)
        changed = True

    if changed:
        save_notification_listener_state()


async def openclaw_voice_session_listener_loop() -> None:
    if not NOTIFICATION_LISTENER_ENABLED:
        print("[NOTIFY] OpenClaw session listener disabled")
        return

    session_key = _openclaw_voice_session_key()
    load_notification_listener_state(session_key)
    reconnect_delay = NOTIFICATION_RECONNECT_MIN_SEC

    while True:
        ws: Any | None = None
        try:
            if not await _wait_for_openclaw_transport():
                continue
            ws = await _openclaw_listener_connect()
            print(
                f"[NOTIFY] OpenClaw listener connected ws={_openclaw_gateway_ws_url()} "
                f"session={session_key}"
            )

            _, dirty = await _listener_rpc(
                ws,
                "sessions.messages.subscribe",
                {"key": session_key, "agentId": OPENCLAW_VOICE_AGENT_ID},
                session_key=session_key,
            )
            print(f"[NOTIFY] subscribed session={session_key}")

            # A one-time session-index subscription gives us a second invalidation
            # signal across reset/compaction without polling.
            try:
                _, saw_change = await _listener_rpc(
                    ws,
                    "sessions.subscribe",
                    {},
                    session_key=session_key,
                )
                dirty = dirty or saw_change
            except Exception as exc:
                print(
                    f"[NOTIFY-WARN] sessions.subscribe unavailable; "
                    f"message subscription remains active: {type(exc).__name__}: {exc}"
                )

            history, saw_event = await _listener_rpc(
                ws,
                "chat.history",
                {
                    "sessionKey": session_key,
                    "agentId": OPENCLAW_VOICE_AGENT_ID,
                    "limit": NOTIFICATION_HISTORY_LIMIT,
                    "maxChars": 12000,
                },
                session_key=session_key,
            )
            dirty = dirty or saw_event
            await _reconcile_voice_session_history(history, session_key)
            reconnect_delay = NOTIFICATION_RECONNECT_MIN_SEC

            while True:
                # If a session event raced the synchronous /v1/chat/completions
                # voice turn, do not block on the next WebSocket event. Recheck
                # locally until openclaw_chat() has registered the exact reply
                # suppression hash and released the in-flight gate.
                if dirty and VOICE_OPENCLAW_INFLIGHT > 0:
                    await asyncio.sleep(0.10)
                    continue

                if dirty and VOICE_OPENCLAW_INFLIGHT == 0:
                    await asyncio.sleep(0.12)
                    history, saw_event = await _listener_rpc(
                        ws,
                        "chat.history",
                        {
                            "sessionKey": session_key,
                            "agentId": OPENCLAW_VOICE_AGENT_ID,
                            "limit": NOTIFICATION_HISTORY_LIMIT,
                            "maxChars": 12000,
                        },
                        session_key=session_key,
                    )
                    dirty = saw_event
                    await _reconcile_voice_session_history(history, session_key)
                    continue

                raw = await ws.recv()
                frame = json.loads(raw)
                if not isinstance(frame, dict):
                    continue
                if frame.get("type") != "event":
                    continue
                event_name = str(frame.get("event") or "")
                if event_name == "shutdown":
                    raise RuntimeError("OpenClaw Gateway announced shutdown")
                if _listener_event_targets_voice_session(frame, session_key):
                    dirty = True

        except asyncio.CancelledError:
            if ws is not None:
                await ws.close()
            raise
        except Exception as exc:
            print(
                f"[NOTIFY-WARN] OpenClaw listener disconnected: "
                f"{type(exc).__name__}: {exc}; retry_in={reconnect_delay:.0f}s"
            )
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(
                NOTIFICATION_RECONNECT_MAX_SEC,
                max(NOTIFICATION_RECONNECT_MIN_SEC, reconnect_delay * 2.0),
            )


async def _cleanup_openclaw_info_session(
    session_key: str,
    *,
    category: str,
    attempt: int | None = None,
) -> dict[str, Any]:
    attempt_tag = f"attempt{int(attempt or 0):02d}"
    result: dict[str, Any] = {
        "session_key": session_key,
        "enabled": OPENCLAW_INFO_SESSION_CLEANUP,
        "method": "openclaw_gateway_rpc_sessions_delete",
        "gateway_ws": _openclaw_gateway_ws_url(),
        "status": "skipped",
    }

    if not OPENCLAW_INFO_SESSION_CLEANUP:
        print(
            f"[INFO-CLEANUP] category={category} attempt={int(attempt or 0)} "
            f"session={session_key} status=disabled"
        )
        _write_startup_snapshot_json(
            f"{category}_{attempt_tag}_session_cleanup.json", result
        )
        return result

    if not _is_safe_info_session_key(session_key):
        result.update({"status": "refused", "error": "unsafe_session_key"})
        print(f"[INFO-CLEANUP-WARN] refused unsafe session key={session_key!r}")
        _write_startup_snapshot_json(
            f"{category}_{attempt_tag}_session_cleanup.json", result
        )
        return result

    try:
        payload = await _openclaw_gateway_rpc(
            "sessions.delete",
            {"key": session_key, "deleteTranscript": True},
            timeout=OPENCLAW_INFO_CLEANUP_TIMEOUT_SEC,
        )
        result.update({"status": "deleted", "rpc_payload": payload})
        print(
            f"[INFO-CLEANUP] category={category} attempt={int(attempt or 0)} "
            f"session={session_key} status=deleted transport=gateway-rpc"
        )
    except asyncio.TimeoutError:
        result.update({"status": "failed", "error": "cleanup_timeout"})
        print(
            f"[INFO-CLEANUP-WARN] category={category} "
            f"attempt={int(attempt or 0)} session={session_key} "
            "transport=gateway-rpc timeout"
        )
    except Exception as exc:
        result.update({
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(
            f"[INFO-CLEANUP-WARN] category={category} "
            f"attempt={int(attempt or 0)} session={session_key} "
            f"transport=gateway-rpc error={type(exc).__name__}: {exc}"
        )

    _write_startup_snapshot_json(
        f"{category}_{attempt_tag}_session_cleanup.json", result
    )
    return result


async def _openclaw_background_json(
    prompt: str,
    *,
    category: str,
    attempt: int | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    session_key = _new_info_session_key(category)
    headers = {
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_key,
    }
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"

    # Keep the body free of `user`. The explicit per-request session key exists
    # only so HomeAIAgent can delete that exact transient session afterwards.
    payload = {
        "model": OPENCLAW_MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [_info_delivery_tool_spec()],
        "tool_choice": {
            "type": "function",
            "function": {"name": INFO_SKILL_DELIVERY_TOOL},
        },
    }

    attempt_tag = f"attempt{int(attempt or 0):02d}"
    _write_startup_snapshot_json(
        f"{category}_{attempt_tag}_openclaw_request.json",
        payload,
    )
    _write_startup_snapshot_json(
        f"{category}_{attempt_tag}_openclaw_session.json",
        {
            "session_key": session_key,
            "user_omitted": True,
            "cleanup_enabled": OPENCLAW_INFO_SESSION_CLEANUP,
        },
    )
    print(
        f"[INFO-SKILL] category={category} attempt={int(attempt or 0)} "
        f"openclaw_session=ephemeral key={session_key} user=omitted"
    )

    body: dict[str, Any]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                # Category-level retry already exists. Keep exactly one HTTP
                # request per transient session key so a transport retry cannot
                # accidentally reuse a partially-running session.
                response = await _post_with_retry(
                    client,
                    f"{OPENCLAW_BASE_URL}/v1/chat/completions",
                    attempts=1,
                    headers=headers,
                    json=payload,
                )
            except httpx.HTTPStatusError as exc:
                response = exc.response
                if response is not None:
                    _write_startup_snapshot_json(
                        f"{category}_{attempt_tag}_openclaw_http_meta.json",
                        {
                            "status_code": response.status_code,
                            "url": str(response.request.url),
                            "headers": dict(response.headers),
                            "session_key": session_key,
                        },
                    )
                    _write_startup_snapshot_text(
                        f"{category}_{attempt_tag}_openclaw_http.txt",
                        response.text,
                    )
                raise
            _write_startup_snapshot_json(
                f"{category}_{attempt_tag}_openclaw_http_meta.json",
                {
                    "status_code": response.status_code,
                    "url": str(response.request.url),
                    "headers": dict(response.headers),
                    "session_key": session_key,
                },
            )
            _write_startup_snapshot_text(
                f"{category}_{attempt_tag}_openclaw_http.txt",
                response.text,
            )
            body = response.json()
    finally:
        await _cleanup_openclaw_info_session(
            session_key, category=category, attempt=attempt
        )

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenClaw returned no choices: {body}")

    choice = choices[0]
    message = choice.get("message") or {}
    finish_reason = str(choice.get("finish_reason") or "")
    tool_calls = message.get("tool_calls") or []

    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if str(function.get("name") or "") != INFO_SKILL_DELIVERY_TOOL:
            continue

        arguments = function.get("arguments", "")
        attempt_tag = f"attempt{int(attempt or 0):02d}"
        if isinstance(arguments, dict):
            _write_startup_snapshot_json(
                f"{category}_{attempt_tag}_tool_arguments.json",
                arguments,
            )
            result = arguments
        else:
            raw_args = str(arguments or "")
            _write_startup_snapshot_text(
                f"{category}_{attempt_tag}_tool_arguments.txt",
                raw_args,
            )
            try:
                result = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                _log_json_decode_context(
                    category=category,
                    stage="tool-arguments",
                    raw=raw_args,
                    exc=exc,
                )
                raise

        if not isinstance(result, dict):
            raise RuntimeError(
                "OpenClaw structured Info delivery arguments are not an object"
            )

        print(
            f"[INFO-SKILL] structured transport=tool_call "
            f"category={category} finish={finish_reason or 'tool_calls'}"
        )
        return result

    # Compatibility fallback for an older/incompatible OpenClaw endpoint.
    # Keep strict validation and record any malformed free-text payload so the
    # failure can be diagnosed precisely instead of silently repaired.
    text = _message_content_text(message)
    attempt_tag = f"attempt{int(attempt or 0):02d}"
    if text:
        _write_startup_snapshot_text(
            f"{category}_{attempt_tag}_message_content.txt",
            text,
        )
    if not text:
        raise RuntimeError(
            "OpenClaw returned neither homeai_info_deliver tool call nor content "
            f"(finish_reason={finish_reason!r})"
        )

    print(
        f"[INFO-SKILL-WARN] structured delivery missing; "
        f"falling back to text JSON category={category} "
        f"finish={finish_reason or 'unknown'}"
    )
    try:
        return _extract_json_object(text)
    except json.JSONDecodeError as exc:
        _log_json_decode_context(
            category=category,
            stage="text-fallback",
            raw=text,
            exc=exc,
        )
        raise


def _category_limit(category: str) -> int:
    if category == "game":
        return INFO_GAME_LIMIT
    if category == "finance":
        return INFO_FINANCE_LIMIT
    return INFO_MAX_ITEMS


def _build_get_feed_request(
    category: str = "all",
    *,
    max_items: int | None = None,
) -> dict[str, Any]:
    category = str(category or "all").strip().lower()
    if category not in {"game", "finance", "all"}:
        raise ValueError(f"invalid info category={category!r}")

    if max_items is None:
        max_items = _category_limit(category)
    max_items = max(0, min(INFO_MAX_ITEMS, int(max_items)))

    request: dict[str, Any] = {
        "protocol_version": INFO_SKILL_PROTOCOL,
        "operation": "get_feed",
        "request_id": f"homeai-feed-{category}-{uuid.uuid4()}",
        "locale": "zh-CN",
        "timezone": INFO_SCHEDULE_TIMEZONE,
        "category": category,
        "max_items": max_items,
        "max_age_hours": INFO_MAX_AGE_HOURS,
    }

    # Keep the legacy fields for backward compatibility. A new Skill should
    # honor `category`; an older Skill may still inspect these fields.
    if category == "all":
        request["category_limits"] = {
            "game": INFO_GAME_LIMIT,
            "finance": INFO_FINANCE_LIMIT,
        }
        request["categories"] = ["game", "finance"]
    else:
        request["category_limits"] = {category: max_items}
        request["categories"] = [category]

    return request


async def call_homeai_info_get_feed(
    category: str = "all",
    *,
    max_items: int | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    request = _build_get_feed_request(category, max_items=max_items)
    attempt_tag = f"attempt{int(attempt or 0):02d}"
    _write_startup_snapshot_json(
        f"{category}_{attempt_tag}_request.json",
        request,
    )

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
4. 如果 category=game，只返回 game；如果 category=finance，只返回 finance。
5. 不要自己编造、补充或替代 Skill 的资讯。
6. 调用 Skill 后，不要把结果重写成普通 assistant 文本。
7. 必须调用客户端函数 homeai_info_deliver，并把 Skill 返回对象原样作为函数参数。
8. 不要总结、改写、补充或省略字段；不要输出 Markdown 或自然语言前后缀。
"""

    body = await _openclaw_background_json(
        prompt,
        category=category,
        attempt=attempt,
    )
    _write_startup_snapshot_json(
        f"{category}_{attempt_tag}_parsed_body.json",
        body,
    )

    # New Skill examples may use "protocol"; old 1.1 payload uses
    # "protocol_version". Accept both without weakening the version gate.
    protocol = body.get("protocol_version") or body.get("protocol")
    if protocol != INFO_SKILL_PROTOCOL:
        raise RuntimeError(
            "HomeAI Info protocol mismatch: "
            f"{protocol!r}"
        )

    operation = body.get("operation")
    if operation not in {None, "", "get_feed"}:
        raise RuntimeError(
            f"HomeAI Info operation mismatch: {operation!r}"
        )

    requested_category = str(category or "all").strip().lower()
    returned_category = str(body.get("category") or "").strip().lower()
    if (
        requested_category in {"game", "finance"}
        and returned_category
        and returned_category != requested_category
    ):
        raise RuntimeError(
            "HomeAI Info category mismatch: "
            f"requested={requested_category!r} returned={returned_category!r}"
        )

    return body


def normalize_skill_feed(
    body: dict[str, Any],
    *,
    expected_category: str | None = None,
    allow_empty: bool = False,
) -> list[FeedItem]:
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

    expected = (
        str(expected_category or "").strip().lower()
        if expected_category
        else None
    )
    if expected not in {None, "game", "finance"}:
        raise ValueError(f"invalid expected category={expected!r}")

    result: list[FeedItem] = []
    seen_ids: set[str] = set()
    counts = {"game": 0, "finance": 0}

    for raw in rows:
        if not isinstance(raw, dict):
            continue

        raw_category = str(raw.get("category") or "").strip().lower()
        if raw_category not in counts:
            continue
        if expected is not None and raw_category != expected:
            # Backward-compatible guard: if an old Skill ignores the new
            # category parameter and returns a mixed feed, retain only the
            # requested category instead of failing the whole refresh.
            continue

        limit = (
            INFO_GAME_LIMIT
            if raw_category == "game"
            else INFO_FINANCE_LIMIT
        )
        if counts[raw_category] >= limit:
            continue

        item_id = str(raw.get("id") or raw.get("item_id") or "").strip()
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
            source=str(
                raw.get("source_name")
                or raw.get("source")
                or ""
            )[:200],
            source_url=str(raw.get("source_url") or "")[:600],
            priority=priority,
            published_at=str(raw.get("published_at") or "")[:64],
            content_hash=str(raw.get("content_hash") or "")[:128],
        )

        result.append(item)
        seen_ids.add(item_id)
        counts[raw_category] += 1

        if expected is not None and len(result) >= _category_limit(expected):
            break
        if expected is None and len(result) >= INFO_MAX_ITEMS:
            break

    if not result and not allow_empty:
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


def _merge_category_feeds() -> list[FeedItem]:
    # Preserve each Skill's internal ranking but keep the visible feed balanced
    # by interleaving game and finance.
    game = FEED_CATEGORY_ITEMS["game"][:INFO_GAME_LIMIT]
    finance = FEED_CATEGORY_ITEMS["finance"][:INFO_FINANCE_LIMIT]

    merged: list[FeedItem] = []
    for index in range(max(len(game), len(finance))):
        if index < len(game):
            merged.append(game[index])
        if index < len(finance):
            merged.append(finance[index])
    return merged[:INFO_MAX_ITEMS]


def _remember_feed_history() -> None:
    for item in FEED_ITEMS:
        FEED_ITEM_HISTORY[item.item_id] = item

    # Keep recent IDs available for get_item / "这个讲讲" even after several
    # two-hour feed rotations.
    if len(FEED_ITEM_HISTORY) > 240:
        keep_ids = {item.item_id for item in FEED_ITEMS}
        old_keys = list(FEED_ITEM_HISTORY.keys())
        for key in old_keys:
            if len(FEED_ITEM_HISTORY) <= 160:
                break
            if key not in keep_ids:
                FEED_ITEM_HISTORY.pop(key, None)


async def _fetch_info_category_with_retries(
    category: str,
    *,
    slot_label: str,
) -> tuple[bool, list[FeedItem], str | None]:
    attempts = len(INFO_CATEGORY_RETRY_DELAYS_SEC)
    last_error: Exception | None = None

    for index, delay_sec in enumerate(INFO_CATEGORY_RETRY_DELAYS_SEC, 1):
        if delay_sec:
            print(
                f"[INFO-SKILL] slot={slot_label} category={category} "
                f"retry_wait={delay_sec}s"
            )
            await asyncio.sleep(delay_sec)

        print(
            f"[INFO-SKILL] slot={slot_label} category={category} "
            f"attempt={index}/{attempts}"
        )

        try:
            body = await call_homeai_info_get_feed(
                category,
                max_items=_category_limit(category),
                attempt=index,
            )

            suggested = body.get("next_refresh_after_sec")
            if suggested is not None:
                print(
                    f"[INFO-SKILL] category={category} "
                    f"skill suggested next={suggested}s; "
                    "ignored by fixed 2h wall-clock schedule"
                )

            items = normalize_skill_feed(
                body,
                expected_category=category,
                allow_empty=True,
            )
            _write_startup_snapshot_json(
                f"{category}_attempt{index:02d}_normalized_items.json",
                [asdict(item) for item in items],
            )
            print(
                f"[INFO-SKILL] category={category} SUCCESS "
                f"count={len(items)} attempt={index}/{attempts}"
            )
            return True, items, None

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            _write_startup_snapshot_text(
                f"{category}_attempt{index:02d}_error.txt",
                f"{type(exc).__name__}: {exc}\n",
            )
            print(
                f"[INFO-SKILL-WARN] category={category} "
                f"attempt={index}/{attempts} failed: "
                f"{type(exc).__name__}: {exc}"
            )

    error_text = (
        f"{type(last_error).__name__}: {last_error}"
        if last_error is not None
        else "unknown error"
    )
    print(
        f"[INFO-SKILL-ERROR] category={category} FAILED "
        f"after={attempts} attempts; keeping category last-good: "
        f"{error_text}"
    )
    return False, FEED_CATEGORY_ITEMS[category], error_text


async def refresh_info_from_skill(
    *,
    force: bool = False,
    slot_label: str | None = None,
) -> bool:
    global FEED_ITEMS, FEED_REVISION, LAST_INFO_REFRESH_DIAGNOSTIC

    if slot_label is None:
        slot_label = datetime.now(_info_schedule_tz()).strftime("%Y-%m-%dT%H:%M")

    success: dict[str, bool] = {}
    errors: dict[str, str | None] = {}

    for category in INFO_CATEGORY_ORDER:
        ok, items, error_text = await _fetch_info_category_with_retries(
            category,
            slot_label=slot_label,
        )
        success[category] = ok
        errors[category] = error_text
        if ok:
            FEED_CATEGORY_ITEMS[category] = items
            FEED_CATEGORY_UPDATED_AT[category] = datetime.now(
                _info_schedule_tz()
            ).isoformat()

    succeeded = [c for c in INFO_CATEGORY_ORDER if success.get(c)]
    failed = [c for c in INFO_CATEGORY_ORDER if not success.get(c)]

    LAST_INFO_REFRESH_DIAGNOSTIC = {
        "slot": slot_label,
        "success": dict(success),
        "errors": dict(errors),
        "succeeded": list(succeeded),
        "failed": list(failed),
    }

    if not succeeded:
        print(
            f"[INFO-SKILL] scheduled refresh FAILED slot={slot_label} "
            "game=last-good finance=last-good"
        )
        return False

    new_items = _merge_category_feeds()
    new_revision = _stable_feed_revision(new_items) if new_items else "empty"

    changed = force or new_revision != FEED_REVISION
    FEED_ITEMS = new_items
    FEED_REVISION = new_revision

    _remember_feed_history()
    save_info_skill_cache()

    if failed:
        print(
            f"[INFO-SKILL] scheduled refresh PARTIAL slot={slot_label} "
            f"success={','.join(succeeded)} "
            f"last_good={','.join(failed)} "
            f"count={len(FEED_ITEMS)} revision={FEED_REVISION}"
        )
    else:
        print(
            f"[INFO-SKILL] scheduled refresh SUCCESS slot={slot_label} "
            f"game={len(FEED_CATEGORY_ITEMS['game'])} "
            f"finance={len(FEED_CATEGORY_ITEMS['finance'])} "
            f"count={len(FEED_ITEMS)} revision={FEED_REVISION}"
        )

    if not changed:
        print(f"[INFO-SKILL] unchanged revision={FEED_REVISION}")

    return changed


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
    # the 2-hour news schedule (including the 01:00-09:00 news quiet window).
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

    # Exact two-hour slots: 09,11,13,15,17,19,21,23,01.
    # After 01:00 the next slot is 09:00. There is no 00:00 refresh.
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


def _desired_display_sleeping() -> bool:
    return display_sleep_window_active()


def _drain_display_ack_queue(session: "ClientSession") -> None:
    while True:
        try:
            session.display_ack_queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def _wait_for_matching_display_ack(
    session: "ClientSession",
    command_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.1, timeout)

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()

        ack = await asyncio.wait_for(
            session.display_ack_queue.get(),
            timeout=remaining,
        )

        if str(ack.get("command_id") or "") != command_id:
            print(
                "[DISPLAY-WARN] stale ack ignored "
                f"expected={command_id} "
                f"got={ack.get('command_id')}"
            )
            continue

        return ack


async def ensure_display_policy(
    session: "ClientSession",
    *,
    reason: str = "night_schedule",
) -> bool:
    """Send sleep/wake policy and require a device execution ACK.

    A command is only successful when the device says status=applied and the
    reported actual sleeping state matches the requested policy.

    If the device is temporarily busy (voice/TTS), it may first report
    status=pending. In that case the Gateway waits for a later applied ACK
    instead of falsely treating "command received" as "command executed".
    """
    desired_sleeping = _desired_display_sleeping()
    requested = "sleep" if desired_sleeping else "wake"
    command_id = f"display-{uuid.uuid4()}"

    session.display_expected_command_id = command_id
    _drain_display_ack_queue(session)

    payload = {
        "type": f"display.{requested}",
        "command_id": command_id,
        "reason": reason,
        "timezone": INFO_SCHEDULE_TIMEZONE,
        "require_ack": True,
    }

    try:
        for attempt in range(1, DISPLAY_COMMAND_MAX_ATTEMPTS + 1):
            try:
                await send_json(session.ws, payload)
            except Exception as exc:
                print(
                    f"[DISPLAY-WARN] command send failed "
                    f"requested={requested} attempt={attempt}/"
                    f"{DISPLAY_COMMAND_MAX_ATTEMPTS}: "
                    f"{type(exc).__name__}: {exc}"
                )
                if attempt >= DISPLAY_COMMAND_MAX_ATTEMPTS:
                    return False
                await asyncio.sleep(1.0)
                continue

            print(
                f"[DISPLAY] command sent requested={requested} "
                f"command_id={command_id} "
                f"attempt={attempt}/{DISPLAY_COMMAND_MAX_ATTEMPTS}"
            )

            try:
                ack = await _wait_for_matching_display_ack(
                    session,
                    command_id,
                    DISPLAY_ACK_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                print(
                    f"[DISPLAY-WARN] ACK timeout requested={requested} "
                    f"command_id={command_id} "
                    f"attempt={attempt}/{DISPLAY_COMMAND_MAX_ATTEMPTS}"
                )
                continue

            status = str(ack.get("status") or "").strip().lower()
            actual_sleeping = bool(ack.get("sleeping"))
            session.display_last_ack = dict(ack)

            if status == "applied":
                if actual_sleeping == desired_sleeping:
                    session.display_last_confirmed_sleeping = actual_sleeping
                    print(
                        f"[DISPLAY] ACK confirmed requested={requested} "
                        f"sleeping={actual_sleeping} "
                        f"command_id={command_id}"
                    )
                    return True

                print(
                    f"[DISPLAY-WARN] ACK state mismatch "
                    f"requested={requested} "
                    f"reported_sleeping={actual_sleeping} "
                    f"command_id={command_id}"
                )
                continue

            if status == "pending":
                print(
                    f"[DISPLAY] ACK pending requested={requested} "
                    f"busy={bool(ack.get('busy'))} "
                    f"command_id={command_id}; waiting for execution"
                )

                # A long TTS response can legitimately keep the screen awake.
                # Wait for a second ACK from the same command after the device
                # actually enters/exits sleep.
                try:
                    final_ack = await _wait_for_matching_display_ack(
                        session,
                        command_id,
                        DISPLAY_APPLY_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    print(
                        f"[DISPLAY-WARN] apply timeout requested={requested} "
                        f"command_id={command_id}"
                    )
                    continue

                final_status = str(
                    final_ack.get("status") or ""
                ).strip().lower()
                final_sleeping = bool(final_ack.get("sleeping"))
                session.display_last_ack = dict(final_ack)

                if (
                    final_status == "applied"
                    and final_sleeping == desired_sleeping
                ):
                    session.display_last_confirmed_sleeping = final_sleeping
                    print(
                        f"[DISPLAY] ACK confirmed after pending "
                        f"requested={requested} sleeping={final_sleeping} "
                        f"command_id={command_id}"
                    )
                    return True

                print(
                    f"[DISPLAY-WARN] final ACK invalid "
                    f"status={final_status!r} "
                    f"sleeping={final_sleeping} "
                    f"command_id={command_id}"
                )
                continue

            print(
                f"[DISPLAY-WARN] command rejected/unknown ACK "
                f"status={status!r} command_id={command_id}"
            )

        print(
            f"[DISPLAY-ERROR] policy NOT confirmed "
            f"requested={requested} command_id={command_id}"
        )
        return False

    finally:
        if session.display_expected_command_id == command_id:
            session.display_expected_command_id = ""


def start_display_policy_task(
    session: "ClientSession",
    *,
    reason: str,
) -> None:
    old_task = session.display_policy_task
    if old_task is not None and not old_task.done():
        old_task.cancel()

    session.display_policy_task = asyncio.create_task(
        ensure_display_policy(session, reason=reason)
    )


async def broadcast_display_policy(
    *,
    reason: str = "night_schedule",
) -> None:
    sessions = list(ACTIVE_SESSIONS)
    if not sessions:
        print(
            f"[DISPLAY] no connected device for policy "
            f"{'SLEEP' if _desired_display_sleeping() else 'WAKE'}"
        )
        return

    results = await asyncio.gather(
        *(
            ensure_display_policy(session, reason=reason)
            for session in sessions
        ),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, Exception):
            print(
                f"[DISPLAY-WARN] policy task failed: "
                f"{type(result).__name__}: {result}"
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

        # Never intentionally run before the exact wall-clock transition.
        while datetime.now(_info_schedule_tz()) < target:
            await asyncio.sleep(0.05)

        try:
            await broadcast_display_policy(reason="night_schedule_transition")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[DISPLAY-WARN] transition failed: "
                f"{type(exc).__name__}: {exc}"
            )


async def display_reconcile_loop() -> None:
    """Periodic non-token watchdog for display policy.

    This costs no LLM/OpenClaw tokens. It simply asks the connected device to
    re-confirm the current sleep/wake policy every five minutes.

    The command is not sent while a confirmed manual wake is in progress on
    the firmware side; the device will return pending/manual state and later
    confirm the final policy.
    """
    while True:
        await asyncio.sleep(DISPLAY_STATUS_RECHECK_SEC)

        for session in list(ACTIVE_SESSIONS):
            try:
                start_display_policy_task(
                    session,
                    reason="periodic_reconcile",
                )
            except Exception as exc:
                print(
                    f"[DISPLAY-WARN] reconcile start failed: "
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


async def _sleep_until_not_early(target: datetime) -> None:
    # asyncio.sleep() may resume a few milliseconds early. Re-check the
    # wall clock so a scheduled poll is never started before the exact slot.
    while True:
        now = datetime.now(_info_schedule_tz())
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 1.0))


async def startup_info_refresh_before_ws() -> None:
    # Startup freshness barrier:
    # refresh game + finance BEFORE the WebSocket server starts accepting
    # device connections. This guarantees the first device sync observes the
    # freshly fetched feed when refresh succeeds, rather than racing against
    # the startup polling task and receiving stale last-good first.
    if not _openclaw_transport_ready():
        print(
            "[INFO-SKILL-WARN] OpenClaw transport unavailable at startup; "
            "opening server with last-good cache and leaving SSH reconnect active"
        )
        return
    startup_now = datetime.now(_info_schedule_tz())
    startup_label = startup_now.strftime("startup-%Y-%m-%dT%H:%M:%S")
    _begin_startup_info_snapshot(
        startup_now,
        trigger="startup",
        slot_label=startup_label,
    )
    print(
        f"[INFO-SKILL] startup barrier refresh start="
        f"{startup_now.isoformat()} slot={startup_label}"
    )
    try:
        changed = await refresh_info_from_skill(
            force=True,
            slot_label=startup_label,
        )
        failed = list(LAST_INFO_REFRESH_DIAGNOSTIC.get("failed") or [])
        succeeded = list(LAST_INFO_REFRESH_DIAGNOSTIC.get("succeeded") or [])
        if failed and succeeded:
            snapshot_status = "partial"
        elif failed and not succeeded:
            snapshot_status = "last-good"
        else:
            snapshot_status = "success" if changed else "last-good"
        if changed:
            print(
                f"[INFO-SKILL] startup barrier refresh READY "
                f"count={len(FEED_ITEMS)} revision={FEED_REVISION}"
            )
        else:
            print(
                f"[INFO-SKILL-WARN] startup barrier refresh used last-good "
                f"count={len(FEED_ITEMS)} revision={FEED_REVISION}"
            )
        _finish_startup_info_snapshot(
            started_at=startup_now,
            status=snapshot_status,
            trigger="startup",
            slot_label=startup_label,
        )
    except asyncio.CancelledError:
        _finish_startup_info_snapshot(
            started_at=startup_now,
            status="cancelled",
            error="asyncio.CancelledError",
            trigger="startup",
            slot_label=startup_label,
        )
        raise
    except Exception as exc:
        # Startup must remain available even if OpenClaw / Info Skill is
        # temporarily unhealthy. The already-loaded last-good cache remains
        # authoritative and will be sent once the server opens.
        _finish_startup_info_snapshot(
            started_at=startup_now,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            trigger="startup",
            slot_label=startup_label,
        )
        print(
            f"[INFO-SKILL-ERROR] startup barrier refresh failure, "
            f"opening server with last-good cache: {type(exc).__name__}: {exc}"
        )


async def info_skill_poll_loop() -> None:
    # A1R12: Gateway restart is cache-only for Info. Do NOT spend tokens on a
    # forced startup refresh. The already-loaded last-good cache is served to
    # the device immediately, and this loop owns all real refreshes at the
    # fixed wall-clock schedule below.

    # Fixed wall-clock schedule remains:
    # 09:00,11:00,13:00,15:00,17:00,19:00,21:00,23:00,01:00.
    # Then remain silent until 09:00. Each slot independently fetches game
    # and finance with bounded retries and per-category last-good fallback.
    while True:
        wait_sec, target = seconds_until_next_info_poll()
        print(
            f"[INFO-SKILL] next scheduled poll="
            f"{target.isoformat()} wait={int(wait_sec)}s"
        )

        await _sleep_until_not_early(target)

        try:
            if not await _wait_for_idle():
                print(
                    "[INFO-SKILL] scheduled poll skipped: "
                    "voice path busy too long"
                )
                continue
            if not _openclaw_transport_ready():
                print(
                    "[INFO-SKILL-WARN] scheduled poll skipped: "
                    "OpenClaw transport unavailable; last-good retained"
                )
                continue

            actual = datetime.now(_info_schedule_tz())
            slot_label = target.strftime("%Y-%m-%dT%H:%M")
            print(
                f"[INFO-SKILL] scheduled poll start="
                f"{actual.isoformat()} slot={slot_label}"
            )

            snapshot_started = actual
            _begin_startup_info_snapshot(
                snapshot_started,
                trigger="scheduled",
                slot_label=slot_label,
            )
            try:
                changed = await refresh_info_from_skill(
                    force=False,
                    slot_label=slot_label,
                )
                failed = list(
                    LAST_INFO_REFRESH_DIAGNOSTIC.get("failed") or []
                )
                succeeded = list(
                    LAST_INFO_REFRESH_DIAGNOSTIC.get("succeeded") or []
                )
                if failed and succeeded:
                    snapshot_status = "partial"
                elif failed and not succeeded:
                    snapshot_status = "last-good"
                else:
                    snapshot_status = "success"
                _finish_startup_info_snapshot(
                    started_at=snapshot_started,
                    status=snapshot_status,
                    trigger="scheduled",
                    slot_label=slot_label,
                )
            except asyncio.CancelledError:
                _finish_startup_info_snapshot(
                    started_at=snapshot_started,
                    status="cancelled",
                    error="asyncio.CancelledError",
                    trigger="scheduled",
                    slot_label=slot_label,
                )
                raise
            except Exception as exc:
                _finish_startup_info_snapshot(
                    started_at=snapshot_started,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    trigger="scheduled",
                    slot_label=slot_label,
                )
                raise

            if changed:
                await broadcast_info_sync_when_idle()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Category-level errors should normally be contained inside
            # refresh_info_from_skill. This is the outer safety net.
            print(
                f"[INFO-SKILL-ERROR] scheduled refresh outer failure, "
                f"keeping all last-good cache: "
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
    # Preserve explicit line breaks supplied by OpenClaw as hard breaks.
    # Within each logical line, normalize incidental whitespace and keep the
    # existing width-based wrapping behavior. Glass2 layout remains frozen at
    # three 11 px body lines in the same positions.
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    logical_lines = normalized.strip().split("\n")

    rendered: list[str] = []
    overflow = False

    for logical_index, raw_line in enumerate(logical_lines):
        line_text = " ".join(raw_line.strip().split())
        if not line_text:
            # Ignore accidental empty lines rather than consuming scarce Glass2
            # rows; the meaningful explicit break between non-empty lines is
            # still preserved because each logical line starts a new row.
            continue

        rest = line_text
        while rest:
            if len(rendered) >= 3:
                overflow = True
                break

            line, next_rest = _fit_line(draw, rest, font, 124)
            if not line:
                # Defensive progress guarantee for an unexpectedly wide glyph.
                line = rest[0]
                next_rest = rest[1:]
            rendered.append(line)
            rest = next_rest

        if overflow:
            break

        # If all three display rows are already occupied, any later non-empty
        # logical line means the title was truncated and needs an ellipsis.
        if len(rendered) >= 3:
            remaining = logical_lines[logical_index + 1:]
            if any(" ".join(v.strip().split()) for v in remaining):
                overflow = True
                break

    if not rendered:
        return "", "", ""

    if overflow:
        ellipsis = "…"
        last = rendered[-1]
        while last and _text_width(draw, last + ellipsis, font) > 124:
            last = last[:-1]
        rendered[-1] = last + ellipsis

    while len(rendered) < 3:
        rendered.append("")
    return rendered[0], rendered[1], rendered[2]


def render_feed_frame(item: FeedItem, index: int, total: int) -> bytes:
    image = Image.new("1", (INFO_FRAME_WIDTH, INFO_FRAME_HEIGHT), 0)
    draw = ImageDraw.Draw(image)

    # Category/header is intentionally NOT rendered.
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


def render_reminder_frame(record: ReminderRecord) -> bytes:
    """Render one high-contrast Glass2 reminder frame on the Mac mini.

    The device remains font-light: arbitrary Chinese reminder text is rasterized
    here exactly like the Info feed and transferred as a 128x64 1-bit frame.
    """
    image = Image.new("1", (INFO_FRAME_WIDTH, INFO_FRAME_HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    title_font = _load_cjk_font(10, bold=True)
    body_font = _load_cjk_font(10)
    small_font = ImageFont.load_default()

    title = record.title.strip() or "提醒"
    title_line, _ = _fit_line(draw, title, title_font, 124)
    draw.text((2, 1), title_line or "提醒", font=title_font, fill=1)
    draw.line((0, 13, 127, 13), fill=1)

    text = " ".join(record.text.strip().split())
    y_positions = (17, 29, 41)
    rest = text
    for line_index, y in enumerate(y_positions):
        if not rest:
            break
        line, next_rest = _fit_line(draw, rest, body_font, 124)
        if line_index == len(y_positions) - 1 and next_rest:
            ellipsis = "…"
            while line and _text_width(draw, line + ellipsis, body_font) > 124:
                line = line[:-1]
            line += ellipsis
            next_rest = ""
        draw.text((2, y), line, font=body_font, fill=1)
        rest = next_rest

    draw.line((0, 53, 127, 53), fill=1)
    draw.text((2, 56), "HOMEAI NOTICE", font=small_font, fill=1)

    data = image.tobytes()
    if len(data) != INFO_FRAME_WIDTH * INFO_FRAME_HEIGHT // 8:
        raise RuntimeError(f"invalid reminder frame size={len(data)}")
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
    ptt_trigger: str = "unknown"
    recording: bool = False
    processing: bool = False
    playback_sequence_active: bool = False
    playback_done_event: asyncio.Event = field(default_factory=asyncio.Event)
    playback_error_event: asyncio.Event = field(default_factory=asyncio.Event)
    playback_slot_ready_event: asyncio.Event = field(default_factory=asyncio.Event)

    # Display policy handshake.
    display_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    display_expected_command_id: str = ""
    display_last_ack: dict[str, Any] = field(default_factory=dict)
    display_last_confirmed_sleeping: bool | None = None
    display_policy_task: Any = None


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
                        f"resource={VOLCENGINE_TTS_RESOURCE_ID} "
                        f"speaker={VOLCENGINE_TTS_VOICE} "
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
    global VOICE_OPENCLAW_INFLIGHT
    VOICE_OPENCLAW_INFLIGHT += 1
    try:
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
        full_text = str(content).strip()
        if not full_text:
            raise RuntimeError("OpenClaw returned empty assistant text")

        # The session listener receives the same assistant row asynchronously.
        # Register the exact stored response before releasing the in-flight gate
        # so it can be marked seen without being spoken twice.
        _register_voice_reply_suppression(full_text)

        text = full_text
        if len(text) > MAX_AGENT_CHARS:
            text = text[:MAX_AGENT_CHARS].rstrip() + "。"
        return text
    finally:
        VOICE_OPENCLAW_INFLIGHT = max(0, VOICE_OPENCLAW_INFLIGHT - 1)


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


def _reminder_session_available(session: ClientSession) -> bool:
    return not (
        session.recording
        or session.processing
        or session.playback_sequence_active
    )


def _reminder_spoken_text(text: str) -> str:
    # OpenClaw's final user-visible assistant output is already the canonical
    # notification wording. Do not prepend or rewrite it here.
    return text.strip()


async def _send_reminder_overlay(
    session: ClientSession,
    record: ReminderRecord,
) -> None:
    """Temporarily reuse the already-stable Info frame transport.

    This deliberately avoids a new StickS3 protocol or firmware path. The
    one-item reminder feed stays on Glass2 while TTS plays, then the canonical
    Info feed is restored. During the night sleep window the display remains
    dark, but the audible reminder still uses the same validated TTS path.
    """
    frame = render_reminder_frame(record)
    revision = f"reminder-{record.reminder_id}"
    await send_json(session.ws, {
        "type": "info.begin",
        "revision": revision,
        "count": 1,
    })
    await send_json(session.ws, {
        "type": "info.item",
        "revision": revision,
        "index": 0,
        "id": record.reminder_id,
        "category": "提醒",
        "headline": record.text,
        "frame_hex": frame.hex(),
    })
    await send_json(session.ws, {
        "type": "info.end",
        "revision": revision,
        "count": 1,
    })
    print(f"[NOTIFY] overlay sent id={record.reminder_id}")


async def _deliver_reminder_to_device(
    session: ClientSession,
    record: ReminderRecord,
) -> None:
    session.processing = True
    overlay_sent = False

    try:
        # Prevent a new PTT turn while the reminder TTS is being prepared.
        await send_state(session.ws, "thinking")
        await _send_reminder_overlay(session, record)
        overlay_sent = True

        spoken = _reminder_spoken_text(record.text)
        tts_started = time.perf_counter()
        pcm, sample_rate, provider = await synthesize_speech(spoken)
        tts_ms = int((time.perf_counter() - tts_started) * 1000)
        print(
            f"[NOTIFY] TTS ready id={record.reminder_id} "
            f"provider={provider} bytes={len(pcm)} ms={tts_ms}"
        )
        await send_pcm_for_playback(session, pcm, sample_rate)
        print(f"[NOTIFY] device playback ACK id={record.reminder_id}")

    finally:
        # Restore the real feed even if synthesis/playback failed. The reminder
        # itself remains in the durable queue and will retry later on failure.
        if overlay_sent:
            try:
                await send_info_sync(session)
                print(f"[NOTIFY] info feed restored id={record.reminder_id}")
            except Exception as exc:
                print(
                    f"[NOTIFY-WARN] feed restore failed id={record.reminder_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
        try:
            await send_state(session.ws, "idle")
        except Exception:
            pass
        session.processing = False


async def reminder_dispatch_loop() -> None:
    while True:
        try:
            now = time.time()
            record: ReminderRecord | None = None

            assert REMINDER_LOCK is not None
            async with REMINDER_LOCK:
                for item in REMINDER_PENDING:
                    if item.next_attempt_at <= now:
                        record = item
                        break

            if record is None:
                await asyncio.sleep(0.5)
                continue

            # Prefer the newest live device connection. Reconnect races can
            # briefly leave an older session in the list. Never broadcast a
            # user reminder to multiple terminals.
            session = next(
                (s for s in reversed(ACTIVE_SESSIONS) if _reminder_session_available(s)),
                None,
            )
            if session is None:
                await asyncio.sleep(0.5)
                continue

            try:
                print(
                    f"[NOTIFY] deliver begin id={record.reminder_id} "
                    f"attempt={record.attempts + 1} text={record.text!r}"
                )
                await _deliver_reminder_to_device(session, record)

                async with REMINDER_LOCK:
                    REMINDER_PENDING[:] = [
                        item for item in REMINDER_PENDING
                        if item.dedup_key != record.dedup_key
                    ]
                    delivered = {
                        "dedup_key": record.dedup_key,
                        "reminder_id": record.reminder_id,
                        "delivered_at": _iso_now(),
                    }
                    REMINDER_DELIVERED.append(delivered)
                    del REMINDER_DELIVERED[:-REMINDER_DELIVERED_HISTORY]
                    REMINDER_DELIVERED_KEYS.clear()
                    REMINDER_DELIVERED_KEYS.update(
                        item["dedup_key"] for item in REMINDER_DELIVERED
                        if item.get("dedup_key")
                    )
                    save_reminder_queue()

                print(
                    f"[NOTIFY] delivered id={record.reminder_id} "
                    f"pending={len(REMINDER_PENDING)}"
                )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record.attempts += 1
                backoff = min(REMINDER_RETRY_MAX_SEC, 2 ** min(record.attempts, 8))
                record.next_attempt_at = time.time() + backoff
                record.last_error = f"{type(exc).__name__}: {exc}"[:300]
                async with REMINDER_LOCK:
                    save_reminder_queue()
                print(
                    f"[NOTIFY-WARN] delivery failed id={record.reminder_id} "
                    f"attempts={record.attempts} retry_in={backoff}s "
                    f"error={record.last_error}"
                )
                try:
                    if session.ws:
                        await send_state(session.ws, "idle")
                except Exception:
                    pass

            await asyncio.sleep(0.2)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[NOTIFY-ERROR] dispatch loop: {type(exc).__name__}: {exc}")
            await asyncio.sleep(1.0)


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
            # Loopback acceptance mode: the user should hear their own voice.
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

                # Do not await here: this coroutine must keep receiving so the
                # device's display.ack can be processed.
                start_display_policy_task(
                    session,
                    reason="device_hello",
                )
                await send_info_sync(session)

            elif kind == "display.ack":
                command_id = str(msg.get("command_id") or "")
                status = str(msg.get("status") or "")
                requested = str(msg.get("requested") or "")
                sleeping = bool(msg.get("sleeping"))
                busy = bool(msg.get("busy"))
                manual_wake = bool(msg.get("manual_wake_active"))

                print(
                    f"[DISPLAY] device ACK requested={requested} "
                    f"status={status} sleeping={sleeping} "
                    f"busy={busy} manual_wake={manual_wake} "
                    f"command_id={command_id}"
                )

                if (
                    session.display_expected_command_id
                    and command_id == session.display_expected_command_id
                ):
                    session.display_ack_queue.put_nowait(dict(msg))
                else:
                    print(
                        f"[DISPLAY-WARN] unexpected/stale device ACK "
                        f"expected={session.display_expected_command_id!r} "
                        f"got={command_id!r}"
                    )

            elif kind == "info.ack":
                print(f"[INFO] device ack revision={FEED_REVISION}")

            elif kind == "ptt.start":
                if session.processing:
                    continue
                session.audio.clear()
                session.context = dict(msg.get("context") or {})
                session.diag_glass_mode = str(msg.get("diag_glass_mode") or "GLASS NORMAL")
                session.ptt_trigger = str(msg.get("trigger") or "unknown")
                session.recording = True
                await send_state(ws, "listening")
                current = (session.context.get("current") or {}).get("headline", "")
                print(
                    f"[PTT] start trigger={session.ptt_trigger} "
                    f"current={current}"
                )

            elif kind == "ptt.stop":
                session.recording = False
                stop_trigger = str(msg.get("trigger") or session.ptt_trigger or "unknown")
                session.ptt_trigger = stop_trigger
                declared = ((msg.get("audio") or {}).get("bytes"))
                print(
                    f"[PTT] stop trigger={stop_trigger} "
                    f"received={len(session.audio)} declared={declared}"
                )
                asyncio.create_task(process_utterance(session))

            elif kind == "ptt.abort":
                session.recording = False
                abort_trigger = str(msg.get("trigger") or session.ptt_trigger or "unknown")
                reason = str(msg.get("reason") or "unknown")
                print(
                    f"[PTT] abort trigger={abort_trigger} reason={reason} "
                    f"received={len(session.audio)}"
                )
                session.audio.clear()
                session.ptt_trigger = abort_trigger
                await send_state(ws, "idle")

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

    except ConnectionClosed as exc:
        # A device-side reconnect is recoverable.  Treat an ungraceful socket
        # close as a normal transport event here so the server does not emit a
        # scary handler traceback while the StickS3 reconnect loop does its job.
        code = getattr(exc, "code", None)
        reason = getattr(exc, "reason", "")
        print(f"[WS] client connection closed code={code} reason={reason!r}")
    finally:
        if session.display_policy_task is not None:
            session.display_policy_task.cancel()
        if session in ACTIVE_SESSIONS:
            ACTIVE_SESSIONS.remove(session)
        print("[WS] client disconnected")


async def preflight(*, require_openclaw: bool = True) -> int:
    print("=== HomeAIAgent Gateway persistent-config preflight ===")
    if (
        VOLCENGINE_TTS_RESOURCE_ID != A1R7_STANDARD_TTS_RESOURCE_ID
        or VOLCENGINE_TTS_VOICE != A1R7_STANDARD_TTS_VOICE
    ):
        print(
            "[FAIL] A1R7 standard TTS guard rejected active pairing: "
            f"resource={VOLCENGINE_TTS_RESOURCE_ID} voice={VOLCENGINE_TTS_VOICE}"
        )
        print(
            "[FAIL] expected resource=seed-tts-2.0 "
            "voice=zh_female_vv_uranus_bigtts"
        )
        return 4
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
    print(
        f"[CFG] OpenClaw transport={OPENCLAW_TRANSPORT} "
        f"ssh={OPENCLAW_SSH_USER}@{OPENCLAW_SSH_HOST} "
        f"local=127.0.0.1:{OPENCLAW_LOCAL_PORT} remote=127.0.0.1:{OPENCLAW_REMOTE_PORT}"
    )
    print(f"[CFG] session user={OPENCLAW_USER}")
    print(f"[CFG] info_skill_protocol={INFO_SKILL_PROTOCOL}")
    print("[CFG] info_skill_transport=structured_tool_call fallback=strict_text")
    print(
        "[CFG] info_skill_openclaw_session=ephemeral-explicit "
        "user=omitted cleanup=after-each-request"
    )
    print(
        f"[CFG] info_skill_cleanup=gateway-rpc enabled={OPENCLAW_INFO_SESSION_CLEANUP} "
        f"ws={_openclaw_gateway_ws_url()} method=sessions.delete "
        f"scope=operator.admin ssh=disabled"
    )
    print("[CFG] info_startup_refresh=disabled(cache-only); wall_clock_schedule=09,11,13,15,17,19,21,23,01")
    print(f"[CFG] info_skill_cache={INFO_SKILL_CACHE_FILE}")
    print(
        f"[CFG] info_refresh_snapshots={INFO_SKILL_STARTUP_SNAPSHOT_DIR} "
        f"triggers=scheduled keep={INFO_SKILL_STARTUP_SNAPSHOT_KEEP}"
    )
    print(
        f"[CFG] info_skill_feed={INFO_GAME_LIMIT} game + "
        f"{INFO_FINANCE_LIMIT} finance / max {INFO_MAX_ITEMS}"
    )
    print(
        f"[CFG] info_schedule_tz={INFO_SCHEDULE_TIMEZONE} "
        "hours=09,11,13,15,17,19,21,23,01"
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
    print(
        f"[CFG] notification_listener enabled={NOTIFICATION_LISTENER_ENABLED} "
        f"session={_openclaw_voice_session_key()} "
        f"transport=gateway-ws-outbound"
    )
    print(f"[CFG] notification_queue={REMINDER_QUEUE_FILE}")
    print(f"[CFG] notification_state={NOTIFICATION_STATE_FILE}")

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
        level = "FAIL" if require_openclaw else "WARN"
        print(f"[{level}] OpenClaw /v1/models: {type(exc).__name__}: {exc}")
        if require_openclaw:
            return 3
        print("[DEGRADED] HomeAIAgent will stay online while embedded SSH reconnects.")
        print("[READY] Speech/device services are ready; OpenClaw is temporarily unavailable.")
        return 0

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
    global REMINDER_LOCK
    global OPENCLAW_TRANSPORT_MANAGER

    if (
        VOLCENGINE_TTS_RESOURCE_ID != A1R7_STANDARD_TTS_RESOURCE_ID
        or VOLCENGINE_TTS_VOICE != A1R7_STANDARD_TTS_VOICE
    ):
        raise RuntimeError(
            "A1R7 standard TTS guard rejected active pairing: "
            f"resource={VOLCENGINE_TTS_RESOURCE_ID} voice={VOLCENGINE_TTS_VOICE}"
        )

    REMINDER_LOCK = asyncio.Lock()
    load_reminder_queue()
    load_info_skill_cache()
    load_gold_quote_cache()

    OPENCLAW_TRANSPORT_MANAGER = OpenClawTransportManager(_openclaw_transport_config())
    transport_task = asyncio.create_task(OPENCLAW_TRANSPORT_MANAGER.run())

    transport_ready = await _wait_for_openclaw_transport(OPENCLAW_SSH_STARTUP_WAIT_SEC)
    if transport_task.done():
        exc = transport_task.exception()
        if exc is not None:
            raise RuntimeError(f"OpenClaw transport manager stopped: {type(exc).__name__}: {exc}")
    if transport_ready:
        print("[OPENCLAW-TRANSPORT] ready before startup preflight")
    else:
        detail = OPENCLAW_TRANSPORT_MANAGER.last_error or "still connecting"
        print(
            f"[OPENCLAW-TRANSPORT-WARN] not ready after "
            f"{OPENCLAW_SSH_STARTUP_WAIT_SEC:.0f}s ({detail}); "
            "starting HomeAIAgent in degraded mode"
        )

    preflight_status = await preflight(require_openclaw=False)
    if preflight_status != 0:
        transport_task.cancel()
        await OPENCLAW_TRANSPORT_MANAGER.close()
        raise RuntimeError(f"HomeAIAgent startup preflight failed status={preflight_status}")

    # A1R12: restart is cache-only for Info. The cache was already loaded by
    # load_info_skill_cache(); do not call OpenClaw/Info Skill here. This avoids
    # spending tokens every time the Gateway is restarted during development.
    # The next real refresh is owned exclusively by info_skill_poll_loop() at
    # the fixed wall-clock slots.
    print(
        f"[INFO-SKILL] startup cache-only count={len(FEED_ITEMS)} "
        f"revision={FEED_REVISION}; next refresh follows wall-clock schedule"
    )

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
        display_reconcile_task = asyncio.create_task(display_reconcile_loop())
        reminder_task = asyncio.create_task(reminder_dispatch_loop())
        notification_listener_task = asyncio.create_task(openclaw_voice_session_listener_loop())
        try:
            await asyncio.Future()
        finally:
            info_task.cancel()
            gold_task.cancel()
            display_task.cancel()
            display_reconcile_task.cancel()
            reminder_task.cancel()
            notification_listener_task.cancel()
            transport_task.cancel()
            if OPENCLAW_TRANSPORT_MANAGER is not None:
                await OPENCLAW_TRANSPORT_MANAGER.close()
            await asyncio.gather(transport_task, return_exceptions=True)


async def check_main() -> int:
    global OPENCLAW_TRANSPORT_MANAGER
    OPENCLAW_TRANSPORT_MANAGER = OpenClawTransportManager(_openclaw_transport_config())
    transport_task = asyncio.create_task(OPENCLAW_TRANSPORT_MANAGER.run())
    try:
        ready = await _wait_for_openclaw_transport(OPENCLAW_SSH_STARTUP_WAIT_SEC)
        if transport_task.done():
            exc = transport_task.exception()
            if exc is not None:
                print(f"[FAIL] OpenClaw transport manager stopped: {type(exc).__name__}: {exc}")
                return 3
        if not ready:
            detail = OPENCLAW_TRANSPORT_MANAGER.last_error or "still connecting"
            print(f"[FAIL] OpenClaw transport not ready: {detail}")
            return 3
        return await preflight(require_openclaw=True)
    finally:
        transport_task.cancel()
        await OPENCLAW_TRANSPORT_MANAGER.close()
        await asyncio.gather(transport_task, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HomeAIAgent voice + OpenClaw Info Skill gateway")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate OpenAI/OpenClaw configuration and exit",
    )
    args = parser.parse_args()

    if args.check:
        raise SystemExit(asyncio.run(check_main()))
    asyncio.run(main())
