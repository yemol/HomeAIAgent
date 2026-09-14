#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
import gzip
import hashlib
import io
import json
import math
import os
import re
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
from urllib.parse import parse_qs, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
import httpx
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.datastructures import Headers
from websockets.http11 import Response
from dotenv import load_dotenv

from openclaw_transport import OpenClawTransportConfig, OpenClawTransportManager
from kitchen_menu import KitchenMenuError, load_kitchen_menu, match_recipe, recipe_for

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

# Runtime diagnostics stay outside the deployable source tree.
HOMEAI_DEBUG_DIR = HOMEAI_DATA_DIR / "debug"

# Keep routine logs compact. Verbose transport/UI chatter and Python tracebacks
# can be re-enabled temporarily from the persistent environment when diagnosing.
HOMEAI_LOG_VERBOSE = os.getenv("HOMEAI_LOG_VERBOSE", "false").strip().lower() in {"1", "true", "yes", "on"}
HOMEAI_LOG_TRACEBACK = os.getenv("HOMEAI_LOG_TRACEBACK", "false").strip().lower() in {"1", "true", "yes", "on"}

def _vlog(message: str) -> None:
    if HOMEAI_LOG_VERBOSE:
        print(message)

def _log_traceback() -> None:
    if HOMEAI_LOG_TRACEBACK:
        traceback.print_exc()

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

# NetworkSpeaker runtime volume control. Relative voice commands use a fixed
# 10-point step so "大声一点 / 轻一点" is predictable instead of model-dependent.
SPEAKER_VOLUME_STEP_PERCENT = 10
SPEAKER_VOLUME_ACK_TIMEOUT_SEC = 2.5

# Audio-output routing. HomeAgent may switch between its own speaker and
# a bound NetworkSpeaker. HomeAgentMini is intentionally NetworkSpeaker-only.
AUDIO_OUTPUT_ROUTE_STATE_FILE = HOMEAI_DATA_DIR / "audio_output_route_state.json"
HOMEAI_DEFAULT_AUDIO_OUTPUT = os.getenv(
    "HOMEAI_DEFAULT_AUDIO_OUTPUT", "network"
).strip().lower()
if HOMEAI_DEFAULT_AUDIO_OUTPUT not in {"local", "network"}:
    HOMEAI_DEFAULT_AUDIO_OUTPUT = "network"

# Once a turn starts on a NetworkSpeaker, keep that sink sticky for the whole
# reply. A transient speaker WebSocket reconnect must never make the remaining
# answer jump to the companion's built-in speaker.
AUDIO_ROUTE_RECONNECT_GRACE_SEC = max(
    0.5, float(os.getenv("HOMEAI_AUDIO_ROUTE_RECONNECT_GRACE_SEC", "8"))
)
AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS = max(1, int(
    os.getenv("HOMEAI_AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS", "3")
))
AUDIO_ROUTE_RECONNECT_POLL_SEC = 0.10

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

# KitchenTerminal A3.0b FIX1. The iPad loads its UI from this same Gateway process.
# KitchenTerminal includes the validated “问逐光” PTT Q&A pipeline.
KITCHEN_PROTOCOL = "homeai-kitchen/1.9"
KITCHEN_UI_VERSION = "A3.0b FIX1 R12"
KITCHEN_HTTP_PATH = os.getenv("HOMEAI_KITCHEN_HTTP_PATH", "/kitchen").strip() or "/kitchen"
KITCHEN_WS_PATH = os.getenv("HOMEAI_KITCHEN_WS_PATH", "/kitchen/ws").strip() or "/kitchen/ws"
KITCHEN_CONTROL_PATH = os.getenv("HOMEAI_KITCHEN_CONTROL_PATH", "/kitchen/control").strip() or "/kitchen/control"
KITCHEN_DEVICE_ID = os.getenv("HOMEAI_KITCHEN_DEVICE_ID", "KitchenTerminal-iPadMini").strip() or "KitchenTerminal-iPadMini"
KITCHEN_MENU_DIR = Path(
    os.getenv(
        "HOMEAI_KITCHEN_MENU_DIR",
        str(Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/自媒体工作流/晚餐推荐"),
    )
).expanduser()
KITCHEN_TIMER_STATE_FILE = HOMEAI_DATA_DIR / "kitchen_timers.json"
KITCHEN_PROGRESS_STATE_FILE = HOMEAI_DATA_DIR / "kitchen_progress.json"
KITCHEN_IDLE_RETURN_DELAY_SEC = max(30, int(os.getenv("HOMEAI_KITCHEN_IDLE_RETURN_DELAY_SEC", "240")))
KITCHEN_TIMER_MIN_SEC = max(1, int(os.getenv("HOMEAI_KITCHEN_TIMER_MIN_SEC", "5")))
KITCHEN_TIMER_MAX_SEC = max(60, int(os.getenv("HOMEAI_KITCHEN_TIMER_MAX_SEC", str(99 * 60 + 59))))
KITCHEN_AUDIO_TTL_SEC = max(30, int(os.getenv("HOMEAI_KITCHEN_AUDIO_TTL_SEC", "600")))
KITCHEN_QA_MAX_SEC = max(5, min(45, int(os.getenv("HOMEAI_KITCHEN_QA_MAX_SEC", "25"))))
KITCHEN_QA_MAX_BYTES = max(128 * 1024, min(2 * 1024 * 1024 - 4096, int(os.getenv("HOMEAI_KITCHEN_QA_MAX_BYTES", "1500000"))))
KITCHEN_AUDIO_CACHE_MAX = max(2, int(os.getenv("HOMEAI_KITCHEN_AUDIO_CACHE_MAX", "8")))
KITCHEN_FINISHED_TIMER_TTL_SEC = max(60, int(os.getenv("HOMEAI_KITCHEN_FINISHED_TIMER_TTL_SEC", "1800")))
KITCHEN_PRIVATE_RECIPE_DIR = Path(
    os.getenv(
        "HOMEAI_KITCHEN_PRIVATE_RECIPE_DIR",
        str(KITCHEN_MENU_DIR.parent / "私房菜"),
    )
).expanduser()


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

STANDARD_TTS_RESOURCE_ID = "seed-tts-2.0"
STANDARD_TTS_VOICE = "zh_female_vv_uranus_bigtts"


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as exc:
        print(f"[FILE-WARN] write_bytes failed path={path} error={type(exc).__name__}: {exc}")


def _best_effort_write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
# Voice Turn Fence. OpenClaw may append multiple assistant progress rows
# to the same voice session while one synchronous /v1/chat/completions request
# is running. The HTTP response is the authoritative spoken answer. Keep a
# fence for each completed synchronous turn so transcript reconciliation marks
# every unseen assistant row up to and including that exact final reply as
# consumed instead of replaying progress rows later as notifications.
VOICE_TURN_FENCES: list[dict[str, Any]] = []
VOICE_TURN_FENCE_TTL_SEC = 600.0
VOICE_TURN_FENCE_MAX = 12


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


def _prune_voice_turn_fences() -> None:
    now = time.time()
    VOICE_TURN_FENCES[:] = [
        item for item in VOICE_TURN_FENCES
        if float(item.get("expires_at") or 0.0) > now
    ][-VOICE_TURN_FENCE_MAX:]


def _register_voice_turn_fence(final_text: str) -> None:
    normalized = _normalize_notification_compare_text(final_text)
    if not normalized:
        return
    _prune_voice_turn_fences()
    VOICE_TURN_FENCES.append({
        "final_digest": _notification_text_digest(normalized),
        "expires_at": time.time() + VOICE_TURN_FENCE_TTL_SEC,
    })
    del VOICE_TURN_FENCES[:-VOICE_TURN_FENCE_MAX]
    print("[VOICE-FENCE] armed for synchronous turn completion")


def _resolve_voice_turn_fences(messages: list[Any]) -> set[str] | None:
    """Resolve completed synchronous voice turns against chat.history.

    Returns a set of assistant message identities which belong to synchronous
    voice-turn progress/final output and therefore must be marked seen without
    entering the async notification queue. If the active fence's exact final
    reply is not present in history yet, return None to hold reconciliation
    until OpenClaw has committed the complete turn.

    The boundary is transcript-native: find the exact final assistant reply,
    then walk backward to the nearest user row. Only assistant rows between
    that user row and the final reply are suppressed. This preserves genuinely
    asynchronous assistant messages which arrived before the voice turn.
    """
    _prune_voice_turn_fences()
    if not VOICE_TURN_FENCES:
        return set()

    suppressed: set[str] = set()
    resolved_count = 0

    for fence in list(VOICE_TURN_FENCES):
        final_digest = str(fence.get("final_digest") or "")
        if not final_digest:
            resolved_count += 1
            continue

        final_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            row = messages[idx]
            if not isinstance(row, dict):
                continue
            if str(row.get("role") or "").strip().lower() != "assistant":
                continue
            text = _history_message_text(row)
            if _notification_text_digest(_normalize_notification_compare_text(text)) == final_digest:
                final_idx = idx
                break

        if final_idx < 0:
            # The HTTP response can return a fraction before the session
            # transcript has committed its final row. Do not queue any new
            # assistant rows in this tiny window; retry on the next event/poll.
            return None

        user_idx = -1
        for idx in range(final_idx - 1, -1, -1):
            row = messages[idx]
            if isinstance(row, dict) and str(row.get("role") or "").strip().lower() == "user":
                user_idx = idx
                break

        if user_idx < 0:
            # A very small chat.history window could omit the preceding user
            # row. Fall back to the beginning of the returned window rather
            # than replaying known voice-turn progress as reminders.
            user_idx = -1
            print("[VOICE-FENCE-WARN] preceding user row missing; using history-window boundary")

        rows = 0
        final_text = ""
        for idx in range(user_idx + 1, final_idx + 1):
            row = messages[idx]
            if not isinstance(row, dict):
                continue
            if str(row.get("role") or "").strip().lower() != "assistant":
                continue
            if not _is_user_visible_async_assistant_message(row):
                continue
            key = _history_message_identity(row)
            if key:
                suppressed.add(key)
                rows += 1
            if idx == final_idx:
                final_text = _history_message_text(row)

        if final_text:
            _consume_voice_reply_suppression(final_text)
        resolved_count += 1
        print(f"[VOICE-FENCE] closed at synchronous reply; suppressed_rows={rows}")

    if resolved_count:
        del VOICE_TURN_FENCES[:resolved_count]
    return suppressed


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
OPENCLAW_CHAT_TIMEOUT_SEC = max(30.0, float(os.getenv("OPENCLAW_CHAT_TIMEOUT_SEC", "180")))
OPENCLAW_KITCHEN_TIMEOUT_SEC = max(30.0, float(os.getenv("OPENCLAW_KITCHEN_TIMEOUT_SEC", "180")))
OPENCLAW_INFO_TIMEOUT_SEC = max(60.0, float(os.getenv("OPENCLAW_INFO_TIMEOUT_SEC", "300")))


def _openclaw_http_trust_env() -> bool:
    """Bypass HTTP(S)_PROXY for loopback OpenClaw endpoints."""
    try:
        host = (urlsplit(OPENCLAW_BASE_URL).hostname or "").strip().lower()
    except Exception:
        host = ""
    return host not in {"127.0.0.1", "localhost", "::1"}


def _openclaw_http_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, trust_env=_openclaw_http_trust_env())


# Multi-device conversation router.
#
# The existing StickS3 does not currently send a device_id, so a missing id is
# intentionally treated as the primary HomeAIAgent. HomeAIAgent Mini already
# announces HOMEAI_DEVICE_ID=homeai-mini-bedroom-01 and therefore receives a
# separate OpenClaw `user`, which resolves to a separate OpenAI-compatible
# conversation session.
HOMEAI_PRIMARY_DEVICE_ID = (
    os.getenv("HOMEAI_PRIMARY_DEVICE_ID", "homeai-agent-main-01").strip()
    or "homeai-agent-main-01"
)
HOMEAI_MINI_DEVICE_ID = (
    os.getenv("HOMEAI_MINI_DEVICE_ID", "homeai-mini-bedroom-01").strip()
    or "homeai-mini-bedroom-01"
)
OPENCLAW_MINI_USER = (
    os.getenv("OPENCLAW_MINI_USER", "home-ai-agent-mini:main").strip()
    or "home-ai-agent-mini:main"
)
HOMEAI_AUTO_ISOLATE_UNKNOWN_COMPANIONS = (
    os.getenv("HOMEAI_AUTO_ISOLATE_UNKNOWN_COMPANIONS", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
HOMEAI_DEVICE_OPENCLAW_USERS_JSON = os.getenv(
    "HOMEAI_DEVICE_OPENCLAW_USERS_JSON", ""
).strip()


def _safe_device_key(device_id: str) -> str:
    cleaned = "".join(
        ch if (ch.isalnum() or ch in {"-", "_", "."}) else "-"
        for ch in str(device_id or "").strip().lower()
    ).strip("-")
    if not cleaned:
        cleaned = hashlib.sha256(
            str(device_id or "unknown").encode("utf-8")
        ).hexdigest()[:12]
    return cleaned[:96]


def _load_device_openclaw_users() -> dict[str, str]:
    mapping: dict[str, str] = {
        HOMEAI_PRIMARY_DEVICE_ID: OPENCLAW_USER,
        HOMEAI_MINI_DEVICE_ID: OPENCLAW_MINI_USER,
    }
    if not HOMEAI_DEVICE_OPENCLAW_USERS_JSON:
        return mapping

    try:
        raw = json.loads(HOMEAI_DEVICE_OPENCLAW_USERS_JSON)
        if not isinstance(raw, dict):
            raise ValueError("mapping must be a JSON object")
        for device_id, user in raw.items():
            did = str(device_id or "").strip()
            target = str(user or "").strip()
            if did and target:
                mapping[did] = target
    except Exception as exc:
        print(
            "[CONFIG-WARN] HOMEAI_DEVICE_OPENCLAW_USERS_JSON ignored: "
            f"{type(exc).__name__}: {exc}"
        )
    return mapping


DEVICE_OPENCLAW_USERS = _load_device_openclaw_users()


def _load_audio_output_routes() -> dict[str, str]:
    routes: dict[str, str] = {}
    try:
        if AUDIO_OUTPUT_ROUTE_STATE_FILE.exists():
            raw = json.loads(AUDIO_OUTPUT_ROUTE_STATE_FILE.read_text(encoding="utf-8"))
            raw_routes = raw.get("routes", {}) if isinstance(raw, dict) else {}
            if isinstance(raw_routes, dict):
                for device_id, mode in raw_routes.items():
                    did = str(device_id or "").strip()
                    value = str(mode or "").strip().lower()
                    if did and value in {"local", "network"}:
                        routes[did] = value
    except Exception as exc:
        print(
            "[AUDIO-ROUTE-WARN] route state load failed: "
            f"{type(exc).__name__}: {exc}"
        )
    return routes


def _persist_audio_output_routes() -> None:
    try:
        AUDIO_OUTPUT_ROUTE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": 1,
            "routes": AUDIO_OUTPUT_ROUTES,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        tmp = AUDIO_OUTPUT_ROUTE_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(AUDIO_OUTPUT_ROUTE_STATE_FILE)
    except Exception as exc:
        print(
            "[AUDIO-ROUTE-WARN] route state save failed: "
            f"{type(exc).__name__}: {exc}"
        )


AUDIO_OUTPUT_ROUTES: dict[str, str] = _load_audio_output_routes()


def _audio_output_mode_for_device(device_id: str) -> str:
    did = str(device_id or "").strip()
    if did == HOMEAI_MINI_DEVICE_ID:
        return "network"
    if did == HOMEAI_PRIMARY_DEVICE_ID:
        return AUDIO_OUTPUT_ROUTES.get(did, HOMEAI_DEFAULT_AUDIO_OUTPUT)
    # Future/unknown companions prefer a bound
    # speaker if present, otherwise use the companion itself.
    return "auto"


def _set_audio_output_mode_for_device(device_id: str, mode: str) -> bool:
    did = str(device_id or "").strip()
    target = str(mode or "").strip().lower()
    if did != HOMEAI_PRIMARY_DEVICE_ID or target not in {"local", "network"}:
        return False
    AUDIO_OUTPUT_ROUTES[did] = target
    _persist_audio_output_routes()
    print(f"[AUDIO-ROUTE] preference device={did} mode={target}")
    return True


def _openclaw_user_for_device(device_id: str) -> str:
    did = str(device_id or "").strip() or HOMEAI_PRIMARY_DEVICE_ID
    mapped = DEVICE_OPENCLAW_USERS.get(did)
    if mapped:
        return mapped

    if HOMEAI_AUTO_ISOLATE_UNKNOWN_COMPANIONS:
        # Stable per-device namespace: unknown future companion terminals do not
        # silently collapse back into the primary conversation.
        return f"home-ai-agent-device:{_safe_device_key(did)}"

    return OPENCLAW_USER

# OpenClaw transport is owned by this Python service. On the Mac mini
# the default is an in-process AsyncSSH local forward. A future deployment on
# the OpenClaw host can switch OPENCLAW_TRANSPORT=direct without changing the
# StickS3 protocol.
OPENCLAW_TRANSPORT = os.getenv("OPENCLAW_TRANSPORT", "embedded_ssh").strip().lower()
OPENCLAW_SSH_USER = os.getenv("OPENCLAW_SSH_USER", "").strip()
OPENCLAW_SSH_HOST = os.getenv("OPENCLAW_SSH_HOST", "").strip()
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


def _openclaw_voice_session_key(openclaw_user: str | None = None) -> str:
    # Mirrors OpenClaw's current OpenAI-compatible resolver exactly:
    # prefix="openai" + stable `user` -> agent:<agentId>:openai-user:<user>.
    user = str(openclaw_user or OPENCLAW_USER)
    return f"agent:{OPENCLAW_VOICE_AGENT_ID}:openai-user:{user}"


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

# NetworkSpeaker (ESP32-C3) uses a deliberately gentler transport profile than
# the PSRAM-equipped StickS3. The shared 768 KiB / two-slot prebuffer was
# originally tuned for StickS3; on NetworkSpeaker, long replies can otherwise
# create a large burst while it is simultaneously doing Wi-Fi RX + I2S output.
# 256 KiB @ PCM16 mono 16 kHz = 8.192 s per segment. Two queued slots therefore
# keep ~16 s of audio ahead while cutting each refill burst by 3x.
NETWORK_SPEAKER_TTS_SEGMENT_MAX_BYTES = int(
    os.getenv("NETWORK_SPEAKER_TTS_SEGMENT_MAX_BYTES", str(256 * 1024))
)
NETWORK_SPEAKER_TTS_CHUNK_BYTES = int(
    os.getenv("NETWORK_SPEAKER_TTS_CHUNK_BYTES", "4096")
)
NETWORK_SPEAKER_TTS_PACE_EVERY_CHUNKS = max(0, int(
    os.getenv("NETWORK_SPEAKER_TTS_PACE_EVERY_CHUNKS", "8")
))
NETWORK_SPEAKER_TTS_PACE_SEC = max(0.0, float(
    os.getenv("NETWORK_SPEAKER_TTS_PACE_SEC", "0.001")
))



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
                "version": KITCHEN_UI_VERSION,
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
                "version": KITCHEN_UI_VERSION,
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

    fenced_keys = _resolve_voice_turn_fences(messages)
    if fenced_keys is None:
        print("[VOICE-FENCE] waiting for synchronous reply commit; reconciliation deferred")
        return

    changed = False
    for raw in assistant_rows:
        if not isinstance(raw, dict):
            continue
        key = _history_message_identity(raw)
        if not key or key in NOTIFICATION_SEEN_SET:
            continue
        text = _history_message_text(raw)

        if key in fenced_keys:
            _remember_notification_seen(key)
            changed = True
            print("[VOICE-FENCE] synchronous progress/final row consumed")
            continue

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
    timeout: float = OPENCLAW_INFO_TIMEOUT_SEC,
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
        async with _openclaw_http_client(timeout) as client:
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
    sessions = [
        s
        for s in ACTIVE_SESSIONS
        if _is_companion_session(s) and _supports_display_policy(s)
    ]
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

        for session in [
            s
            for s in list(ACTIVE_SESSIONS)
            if _is_companion_session(s) and _supports_display_policy(s)
        ]:
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

    for session in [
        s
        for s in list(ACTIVE_SESSIONS)
        if _is_companion_session(s) and _supports_info_feed(s)
    ]:
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




async def info_skill_poll_loop() -> None:
    # Gateway restart is cache-only for Info. Do NOT spend tokens on a
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
    _vlog(f"[INFO] sync sent revision={FEED_REVISION} count={total}")


@dataclass
class ClientSession:
    ws: Any

    # Device identity/routing.
    # - companion: owns an OpenClaw conversation
    # - speaker: owns no LLM session; it is an audio sink bound to parent_device_id
    device_id: str = HOMEAI_PRIMARY_DEVICE_ID
    device_role: str = "companion"
    parent_device_id: str = ""
    openclaw_user: str = OPENCLAW_USER
    audio_priority: int = 0
    capabilities: dict[str, Any] = field(default_factory=dict)
    hello_received: bool = False
    connected_at: float = field(default_factory=time.time)

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
    # Number of fully played segments confirmed by playback.slot_ready/done.
    # This survives a transient disconnect long enough for the route layer to
    # resume from the first unconfirmed segment on the reconnected speaker.
    playback_completed_segments: int = 0
    playback_total_segments: int = 0

    # NetworkSpeaker control ACKs. Commands are serialized by one companion
    # turn, but the request_id check still protects against stale frames.
    speaker_volume_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    # Display policy handshake.
    display_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    display_expected_command_id: str = ""
    display_last_ack: dict[str, Any] = field(default_factory=dict)
    display_last_confirmed_sleeping: bool | None = None
    display_policy_task: Any = None

    # Explicit user voice control for Glass2 only. This is independent from
    # the automatic night policy that can blank both local displays.
    glass2_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    glass2_expected_command_id: str = ""


@dataclass
class KitchenSession:
    ws: Any
    device_id: str = KITCHEN_DEVICE_ID
    hello_received: bool = False
    connected_at: float = field(default_factory=time.time)
    current_view: str = "idle"
    current_item: str = ""
    current_step: int = 0
    # A3.0b one-shot Kitchen Q&A upload. The browser sends a 16 kHz mono WAV
    # over a dedicated WSS connection; normal UI state remains HTTP-poll owned.
    qa_receiving: bool = False
    qa_processing: bool = False
    qa_request_id: str = ""
    qa_audio: bytearray = field(default_factory=bytearray)


@dataclass
class KitchenTimer:
    timer_id: str
    dish: str
    step: int
    duration_sec: int
    status: str = "running"  # running | paused | finished
    started_at: float = 0.0
    ends_at: float = 0.0
    paused_remaining_sec: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    notified: bool = False


KITCHEN_SESSIONS: list[KitchenSession] = []
KITCHEN_CURRENT_VIEW: dict[str, Any] = {
    "type": "kitchen.show_idle",
    "title": "厨房终端",
    "message": "等待逐光发送菜单",
    "revision": "boot",
}
KITCHEN_CURRENT_MENU: dict[str, Any] | None = None
KITCHEN_CURRENT_STATE: dict[str, Any] = {
    "screen": "idle",
    "date": "",
    "dish": "",
    "step": 0,
}
# Per-day, per-dish last viewed step. Gateway-owned so returning to a dish
# continues where the cook left off, even after an iPad page reload or Gateway restart.
KITCHEN_RECIPE_PROGRESS: dict[str, dict[str, int]] = {}
KITCHEN_RETURN_IDLE_AT: float = 0.0

# A3.0b Q&A state lives in Gateway so the iPad can recover by HTTP polling even
# when its dedicated audio-upload WebSocket closes during ASR/OpenClaw/TTS.
KITCHEN_QA_STATE: dict[str, Any] = {
    "status": "idle",
    "request_id": "",
    "transcript": "",
    "answer": "",
    "error": "",
    "audio_event_id": "",
    "started_at": 0.0,
    "updated_at": 0.0,
}


KITCHEN_TIMERS: dict[str, KitchenTimer] = {}

# KitchenTerminal A3.0b FIX1 local-audio state. Audio is synthesized by the Gateway
# and played by the iPad via Web Audio. The latest event is intentionally
# ephemeral; menu voice should not unexpectedly replay hours later.
KITCHEN_AUDIO_CACHE: dict[str, bytes] = {}
KITCHEN_AUDIO_ORDER: list[str] = []
KITCHEN_LATEST_AUDIO: dict[str, Any] = {
    "event_id": "",
    "text": "",
    "kind": "",
    "created_at": 0.0,
    "expires_at": 0.0,
    "provider": "",
    "source_id": "",
}


def _is_speaker_session(session: ClientSession) -> bool:
    return session.device_role == "speaker"


def _is_companion_session(session: ClientSession) -> bool:
    return session.device_role == "companion"


def _is_primary_companion_session(session: ClientSession) -> bool:
    return (
        _is_companion_session(session)
        and session.openclaw_user == OPENCLAW_USER
    )


def _capability_enabled(
    session: ClientSession,
    key: str,
    *,
    default: bool = False,
) -> bool:
    value = session.capabilities.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return default


def _supports_display_policy(session: ClientSession) -> bool:
    # Current primary StickS3 is legacy and already implements display.sleep /
    # display.wake + display.ack, even if it does not advertise capabilities.
    if _is_primary_companion_session(session):
        return True

    # A generic `display: "135x240"` only describes that a device has a screen.
    # It does NOT imply support for the HomeAIAgent night sleep/wake protocol.
    return _capability_enabled(session, "display_policy")


def _supports_info_feed(session: ClientSession) -> bool:
    # Current primary Glass2 path is legacy and already consumes info.* frames.
    if _is_primary_companion_session(session):
        return True
    return _capability_enabled(session, "info_feed")


def _apply_device_hello(session: ClientSession, msg: dict[str, Any]) -> None:
    role = str(
        msg.get("device_role")
        or msg.get("role")
        or "companion"
    ).strip().lower()
    if role not in {"companion", "speaker"}:
        role = "companion"

    device_id = str(msg.get("device_id") or "").strip()
    if not device_id:
        # Backward compatibility for the current StickS3 firmware.
        device_id = (
            HOMEAI_PRIMARY_DEVICE_ID
            if role == "companion"
            else f"speaker-unidentified-{int(session.connected_at * 1000)}"
        )

    parent = str(
        msg.get("parent_device_id")
        or msg.get("parent_device")
        or ""
    ).strip()

    priority_raw = msg.get("audio_priority", 0)
    try:
        priority = max(-100, min(100, int(priority_raw)))
    except (TypeError, ValueError):
        priority = 0

    capabilities = msg.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}

    session.device_id = device_id
    session.device_role = role
    session.parent_device_id = parent
    session.audio_priority = priority
    session.capabilities = dict(capabilities)
    session.hello_received = True
    session.openclaw_user = (
        ""
        if role == "speaker"
        else _openclaw_user_for_device(device_id)
    )


def _speaker_candidates_for(parent_device_id: str) -> list[ClientSession]:
    parent = str(parent_device_id or "").strip()
    if not parent:
        return []

    candidates = [
        item
        for item in ACTIVE_SESSIONS
        if (
            _is_speaker_session(item)
            and item.hello_received
            and item.parent_device_id == parent
            and not item.playback_sequence_active
        )
    ]
    candidates.sort(
        key=lambda item: (item.audio_priority, item.connected_at),
        reverse=True,
    )
    return candidates


async def _wait_for_bound_speaker_reconnect(
    *,
    device_id: str,
    parent_device_id: str,
    previous: ClientSession,
    timeout: float,
) -> ClientSession | None:
    """Wait briefly for the same NetworkSpeaker to establish a fresh session."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        for item in list(ACTIVE_SESSIONS):
            if item is previous:
                continue
            if (
                _is_speaker_session(item)
                and item.hello_received
                and item.device_id == device_id
                and item.parent_device_id == parent_device_id
                and not item.playback_sequence_active
            ):
                return item
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(AUDIO_ROUTE_RECONNECT_POLL_SEC)


def _select_audio_sink(source: ClientSession) -> ClientSession:
    """Resolve the preferred live sink for one companion.

    HomeAgent (primary) has a persistent user-selectable route:
      local   -> always use the built-in HomeAgent speaker
      network -> use its bound NetworkSpeaker when online

    HomeAgentMini is hard-locked to network mode. The strict "no local
    fallback" rule is enforced by ``send_pcm_to_routed_sink`` so callers can
    still inspect the preferred sink without changing the session type.

    Unknown future companions use the automatic routing behavior.
    """
    if not _is_companion_session(source):
        return source

    mode = _audio_output_mode_for_device(source.device_id)
    if mode == "local":
        return source

    candidates = _speaker_candidates_for(source.device_id)
    if candidates:
        return candidates[0]
    return source


@dataclass(frozen=True)
class AudioOutputIntent:
    operation: str
    target: str = ""


def _parse_audio_output_intent(transcript: str) -> AudioOutputIntent | None:
    text = str(transcript or "").strip()
    if not text:
        return None

    compact = re.sub(r"[\s，。,.！!？?、：:；;“”\"'（）()]+", "", text).lower()

    query_phrases = (
        "现在用哪个喇叭", "当前用哪个喇叭", "现在是哪个喇叭",
        "当前发声设备", "现在发声设备", "当前输出设备",
        "声音从哪里出来", "现在从哪里发声", "你现在从哪里出声", "现在谁在发声",
        "现在用的是本机还是网络喇叭", "现在用的是哪个音箱",
    )
    if any(phrase in compact for phrase in query_phrases):
        return AudioOutputIntent("get")

    local_tokens = (
        "本机喇叭", "本地喇叭", "机身喇叭", "本机扬声器",
        "本地扬声器", "自带喇叭", "自己的喇叭", "你自己的喇叭",
        "homeagent喇叭", "homeagent扬声器",
    )
    network_tokens = (
        "networkspeaker", "网络喇叭", "网络音箱", "网络扬声器",
        "duck喇叭", "duck音箱",
    )
    action_tokens = (
        "切到", "切换到", "切回", "换到", "改到", "改用",
        "使用", "用本", "用网", "用你自己", "用自己", "从本", "从网", "走本", "走网",
        "输出到",
    )

    has_action = any(token in compact for token in action_tokens)
    if has_action and any(token in compact for token in local_tokens):
        return AudioOutputIntent("set", "local")
    if has_action and any(token in compact for token in network_tokens):
        return AudioOutputIntent("set", "network")

    # Natural short forms after the wake word. Keep these explicit enough to
    # avoid hijacking ordinary questions mentioning a speaker.
    if compact in {
        "逐光切回本机", "逐光切到本机", "逐光切换到本机", "逐光用本机喇叭",
        "切回本机", "切到本机", "切换到本机", "用本机喇叭",
    }:
        return AudioOutputIntent("set", "local")
    if compact in {
        "逐光切到networkspeaker", "逐光用网络喇叭", "切到networkspeaker",
        "用网络喇叭", "切回网络喇叭",
    }:
        return AudioOutputIntent("set", "network")
    return None


async def _apply_audio_output_voice_command(
    source: ClientSession,
    transcript: str,
) -> str | None:
    intent = _parse_audio_output_intent(transcript)
    if intent is None:
        return None

    if source.device_id == HOMEAI_MINI_DEVICE_ID:
        speakers = _speaker_candidates_for(source.device_id)
        online = bool(speakers)
        if intent.operation == "get":
            return (
                "HomeAgentMini 固定使用 NetworkSpeaker 发声，当前网络喇叭在线。"
                if online
                else "HomeAgentMini 固定使用 NetworkSpeaker 发声，不过当前网络喇叭不在线。"
            )
        if intent.target == "local":
            return "HomeAgentMini 不支持切换到本机喇叭，只能使用 NetworkSpeaker 发声。"
        return (
            "HomeAgentMini 已经固定使用 NetworkSpeaker，无需切换。"
            if online
            else "HomeAgentMini 固定使用 NetworkSpeaker，不过当前网络喇叭不在线。"
        )

    # Only the primary HomeAgent exposes this user-facing switch. Unknown
    # companions keep their existing routing behavior and let OpenClaw answer.
    if source.device_id != HOMEAI_PRIMARY_DEVICE_ID:
        return None

    mode = _audio_output_mode_for_device(source.device_id)
    speakers = _speaker_candidates_for(source.device_id)
    network_online = bool(speakers)

    if intent.operation == "get":
        if mode == "local":
            return "现在使用的是 HomeAgent 本机喇叭。"
        if network_online:
            return "现在使用的是 NetworkSpeaker。"
        return "当前设置为 NetworkSpeaker，不过它现在不在线，暂时由 HomeAgent 本机喇叭发声。"

    if intent.target == "local":
        _set_audio_output_mode_for_device(source.device_id, "local")
        return "好的，已经切到 HomeAgent 本机喇叭。"

    _set_audio_output_mode_for_device(source.device_id, "network")
    if network_online:
        return "好的，已经切到 NetworkSpeaker。"
    return "已经切到 NetworkSpeaker 模式，不过它现在不在线，暂时由 HomeAgent 本机喇叭发声；连接恢复后会自动切回网络喇叭。"


@dataclass(frozen=True)
class Glass2DisplayIntent:
    sleeping: bool


def _parse_glass2_display_intent(transcript: str) -> Glass2DisplayIntent | None:
    text = str(transcript or "").strip()
    if not text:
        return None

    compact = re.sub(r"[\s，。,.！!？?、：:；;“”\"'（）()]+", "", text).lower()

    # Avoid hijacking negated or informational questions. The local command is
    # intentionally narrow and action-oriented.
    if any(token in compact for token in ("不要", "别", "不用", "不许", "怎么", "如何", "为什么")):
        return None

    for prefix in ("逐光", "请", "帮我", "给我", "麻烦"):
        if compact.startswith(prefix):
            compact = compact[len(prefix):]
    for suffix in ("一下", "吧", "谢谢"):
        if compact.endswith(suffix):
            compact = compact[:-len(suffix)]

    close_phrases = {
        "关闭屏幕", "关掉屏幕", "把屏幕关掉", "关屏", "息屏", "熄屏",
        "关闭glass2", "关掉glass2", "glass2关掉",
    }
    open_phrases = {
        "打开屏幕", "开启屏幕", "把屏幕打开", "亮屏", "点亮屏幕",
        "打开glass2", "开启glass2", "glass2打开",
    }

    if compact in close_phrases:
        return Glass2DisplayIntent(True)
    if compact in open_phrases:
        return Glass2DisplayIntent(False)
    return None


async def _apply_glass2_display_voice_command(
    source: ClientSession,
    transcript: str,
) -> tuple[str, bool] | None:
    intent = _parse_glass2_display_intent(transcript)
    if intent is None:
        return None

    if source.device_id != HOMEAI_PRIMARY_DEVICE_ID:
        return None

    if intent.sleeping:
        return "好的，Glass2 屏幕已关闭。", True
    return "好的，Glass2 屏幕已打开。", False


async def _apply_glass2_target(source: ClientSession, sleeping: bool) -> bool:
    requested = "sleep" if sleeping else "wake"
    command_id = f"glass2-{uuid.uuid4()}"
    source.glass2_expected_command_id = command_id

    while True:
        try:
            source.glass2_ack_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    try:
        await send_json(source.ws, {
            "type": f"glass2.{requested}",
            "command_id": command_id,
            "reason": "local_voice_command",
        })
        print(
            f"[GLASS2-VOICE] command sent requested={requested} "
            f"command_id={command_id}"
        )

        ack = await asyncio.wait_for(source.glass2_ack_queue.get(), timeout=3.0)
        status = str(ack.get("status") or "").strip().lower()
        visible = bool(ack.get("visible"))
        expected_visible = not sleeping
        if status == "applied" and visible == expected_visible:
            print(
                f"[GLASS2-VOICE] ACK confirmed requested={requested} "
                f"visible={visible} command_id={command_id}"
            )
            return True

        print(
            f"[GLASS2-VOICE-WARN] invalid ACK requested={requested} "
            f"status={status!r} visible={visible} command_id={command_id}"
        )
        return False
    except Exception as exc:
        print(f"[GLASS2-VOICE-ERROR] {type(exc).__name__}: {exc}")
        return False
    finally:
        if source.glass2_expected_command_id == command_id:
            source.glass2_expected_command_id = ""


@dataclass(frozen=True)
class SpeakerVolumeIntent:
    operation: str
    value: int = 0


def _parse_zh_number_0_100(text: str) -> int | None:
    """Parse the small Chinese-number subset useful for spoken percentages."""
    value = str(text or "").strip()
    if not value:
        return None
    if value == "一百":
        return 100
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if "十" in value:
        left, right = value.split("十", 1)
        tens = 1 if left == "" else digits.get(left)
        ones = 0 if right == "" else digits.get(right)
        if tens is None or ones is None:
            return None
        result = tens * 10 + ones
        return result if 0 <= result <= 99 else None
    return None


def _parse_speaker_volume_intent(transcript: str) -> SpeakerVolumeIntent | None:
    text = str(transcript or "").strip()
    if not text:
        return None

    compact = re.sub(r"[\s，。,.！!？?、：:；;“”\"'（）()]+", "", text).lower()
    has_volume_context = any(
        token in compact for token in ("逐光", "音量", "声音", "喇叭")
    )
    standalone_followups = {
        "大声一点", "小声一点", "轻一点", "再大一点", "再小一点", "再轻一点",
        "调大一点", "调小一点", "调高一点", "调低一点",
    }
    if compact in standalone_followups:
        has_volume_context = True

    # Explicit absolute percentage, e.g. “音量调到 50% / 百分之五十”.
    if has_volume_context:
        m = re.search(
            r"(?:音量|声音|喇叭).{0,8}?(?:调到|调成|调整到|设为|设置为|到)"
            r"\s*(?:百分之)?\s*(\d{1,3})\s*%?",
            text,
        )
        if m:
            level = int(m.group(1))
            if 0 <= level <= 100:
                return SpeakerVolumeIntent("set", level)

        m = re.search(
            r"(?:音量|声音|喇叭).{0,8}?(?:调到|调成|调整到|设为|设置为|到)"
            r"\s*百分之\s*([零〇一二两三四五六七八九十百]+)",
            text,
        )
        if m:
            level = _parse_zh_number_0_100(m.group(1))
            if level is not None:
                return SpeakerVolumeIntent("set", level)

        if any(token in compact for token in ("最大音量", "声音最大", "开到最大", "调到最大")):
            return SpeakerVolumeIntent("set", 100)

        if any(token in compact for token in ("音量多少", "现在音量", "当前音量", "声音多大")):
            return SpeakerVolumeIntent("get", 0)

    louder = (
        "大声一点", "声音大一点", "音量大一点", "调大一点", "调高一点",
        "音量调高", "声音调高", "再大一点", "再响一点", "响一点",
    )
    softer = (
        "轻一点", "小声一点", "声音小一点", "音量小一点", "调小一点",
        "调低一点", "音量调低", "声音调低", "再小一点", "再轻一点",
    )
    if has_volume_context and any(token in compact for token in louder):
        return SpeakerVolumeIntent("adjust", SPEAKER_VOLUME_STEP_PERCENT)
    if has_volume_context and any(token in compact for token in softer):
        return SpeakerVolumeIntent("adjust", -SPEAKER_VOLUME_STEP_PERCENT)
    return None


def _speaker_supports_volume_control(session: ClientSession) -> bool:
    return (
        _is_speaker_session(session)
        and _capability_enabled(session, "volume_control", default=False)
    )


async def _wait_speaker_volume_ack(
    speaker: ClientSession,
    request_id: str,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + SPEAKER_VOLUME_ACK_TIMEOUT_SEC
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("NetworkSpeaker volume ACK timeout")
        ack = await asyncio.wait_for(
            speaker.speaker_volume_ack_queue.get(),
            timeout=remaining,
        )
        if str(ack.get("request_id") or "") == request_id:
            return ack
        print(
            f"[VOLUME-WARN] stale ACK ignored expected={request_id} "
            f"got={ack.get('request_id')!r}"
        )


async def _apply_speaker_volume_voice_command(
    source: ClientSession,
    transcript: str,
) -> str | None:
    intent = _parse_speaker_volume_intent(transcript)
    if intent is None:
        return None

    sink = _select_audio_sink(source)
    if sink is source or not _speaker_supports_volume_control(sink):
        print(f"[VOLUME] command requested but no controllable speaker source={source.device_id}")
        return "现在没有连接可调音量的网络喇叭。"

    request_id = f"volume-{uuid.uuid4()}"
    payload: dict[str, Any] = {
        "request_id": request_id,
        "device_id": sink.device_id,
    }
    if intent.operation == "set":
        payload.update({
            "type": "speaker.volume.set",
            "volume_percent": max(0, min(100, intent.value)),
        })
    elif intent.operation == "adjust":
        payload.update({
            "type": "speaker.volume.adjust",
            "delta_percent": intent.value,
        })
    else:
        payload["type"] = "speaker.volume.get"

    try:
        await send_json(sink.ws, payload)
        ack = await _wait_speaker_volume_ack(sink, request_id)
    except Exception as exc:
        print(f"[VOLUME-ERROR] {type(exc).__name__}: {exc}")
        return "网络喇叭这次没有响应音量调整。"

    status = str(ack.get("status") or "")
    try:
        level = max(0, min(100, int(ack.get("volume_percent"))))
    except (TypeError, ValueError):
        return "网络喇叭返回的音量状态不正确。"

    sink.capabilities["volume_percent"] = level
    if status not in {"applied", "applied_not_persisted"}:
        return "网络喇叭没有接受这次音量调整。"

    if intent.operation == "get":
        return f"网络喇叭现在音量是百分之{level}。"
    if intent.operation == "adjust":
        direction = "大" if intent.value > 0 else "小"
        return f"好的，调{direction}了一点，现在是百分之{level}。"
    return f"好的，网络喇叭音量已经调到百分之{level}。"


def _device_ready_payload(session: ClientSession) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "gateway.ready",
        "mode": MODE,
        "device_id": session.device_id,
        "device_role": session.device_role,
    }
    if _is_speaker_session(session):
        payload["parent_device_id"] = session.parent_device_id
        payload["audio_protocol"] = "homeai-tts-pcm16/1"
    else:
        payload["conversation_isolated"] = (
            session.openclaw_user != OPENCLAW_USER
        )
        payload["display_policy"] = _supports_display_policy(session)
        payload["info_feed"] = _supports_info_feed(session)
    return payload


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
    kitchen_context = _kitchen_agent_context_text()
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

{kitchen_context}

KitchenTerminal 指代规则：
- 当 KitchenTerminal 正在显示某道菜时，“这个 / 这道菜 / 现在这个”优先指当前菜品。
- 当 KitchenTerminal 正在显示某一步时，“这个要多久 / 现在要怎么做 / 为什么这样做”等追问优先结合当前步骤和该菜完整做法回答。
- 不要声称已经操作 KitchenTerminal，除非 Gateway 本地命令已经实际完成；普通知识问答只负责回答。

用户说：{transcript}
"""


async def openclaw_chat(
    transcript: str,
    context: dict[str, Any],
    *,
    openclaw_user: str = OPENCLAW_USER,
) -> str:
    global VOICE_OPENCLAW_INFLIGHT

    # The notification listener subscribes only to the primary HomeAIAgent
    # session. Mini/other companion turns must not pause or alter that listener.
    notification_tracked = openclaw_user == OPENCLAW_USER
    if notification_tracked:
        VOICE_OPENCLAW_INFLIGHT += 1
    try:
        headers = {"Content-Type": "application/json"}
        if OPENCLAW_TOKEN:
            headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"

        payload = {
            "model": OPENCLAW_MODEL,
            "user": openclaw_user,
            "stream": False,
            "messages": [
                {"role": "user", "content": build_agent_prompt(transcript, enrich_info_context(context))}
            ],
        }
        async with _openclaw_http_client(OPENCLAW_CHAT_TIMEOUT_SEC) as client:
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

        # Only the primary HomeAIAgent conversation is subscribed by the
        # notification listener. Mini and future isolated companion sessions
        # therefore do not participate in the primary suppression/fence state.
        if notification_tracked:
            _register_voice_reply_suppression(full_text)
            _register_voice_turn_fence(full_text)

        text = full_text
        if len(text) > MAX_AGENT_CHARS:
            text = text[:MAX_AGENT_CHARS].rstrip() + "。"
        return text
    finally:
        if notification_tracked:
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


def _tts_segment_max_bytes_for_session(session: ClientSession) -> int:
    if _is_speaker_session(session):
        return max(32 * 1024, NETWORK_SPEAKER_TTS_SEGMENT_MAX_BYTES)
    return DEVICE_TTS_SEGMENT_MAX_BYTES


def _tts_chunk_bytes_for_session(session: ClientSession) -> int:
    if _is_speaker_session(session):
        return max(1024, NETWORK_SPEAKER_TTS_CHUNK_BYTES)
    return TTS_CHUNK_BYTES


def _tts_pacing_for_session(session: ClientSession) -> tuple[int, float]:
    if _is_speaker_session(session):
        return (
            NETWORK_SPEAKER_TTS_PACE_EVERY_CHUNKS,
            NETWORK_SPEAKER_TTS_PACE_SEC,
        )
    return 0, 0.0


async def _send_pcm_segment(
    session: ClientSession,
    pcm: bytes,
    sample_rate: int,
    *,
    index: int,
    total: int,
    chunk_bytes: int | None = None,
    pace_every_chunks: int = 0,
    pace_sec: float = 0.0,
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

    if chunk_bytes is None:
        chunk_bytes = TTS_CHUNK_BYTES
    chunk_bytes = max(1024, int(chunk_bytes))
    chunk_no = 0
    for offset in range(0, len(pcm), chunk_bytes):
        await session.ws.send(pcm[offset:offset + chunk_bytes])
        chunk_no += 1
        if (
            pace_every_chunks > 0
            and pace_sec > 0
            and chunk_no % pace_every_chunks == 0
        ):
            # Tiny cooperative pacing keeps constrained ESP32-C3 receive/I2S
            # tasks responsive without materially changing transfer latency.
            await asyncio.sleep(pace_sec)

    await send_json(session.ws, {
        "type": "tts.end",
        "segment_index": index,
        "segment_total": total,
    })

    _vlog(
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
    *,
    emit_state: bool = True,
) -> None:
    segment_max_bytes = _tts_segment_max_bytes_for_session(session)
    chunk_bytes = _tts_chunk_bytes_for_session(session)
    pace_every_chunks, pace_sec = _tts_pacing_for_session(session)
    segments = split_pcm_for_device(
        pcm,
        sample_rate,
        max_bytes=segment_max_bytes,
    )
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
        f"segments={total} max_segment={segment_max_bytes} "
        f"chunk={chunk_bytes} role={session.device_role}"
    )

    session.playback_sequence_active = True
    session.playback_total_segments = total
    session.playback_completed_segments = 0
    session.playback_done_event.clear()
    session.playback_error_event.clear()
    session.playback_slot_ready_event.clear()

    try:
        if emit_state:
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
                chunk_bytes=chunk_bytes,
                pace_every_chunks=pace_every_chunks,
                pace_sec=pace_sec,
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
                chunk_bytes=chunk_bytes,
                pace_every_chunks=pace_every_chunks,
                pace_sec=pace_sec,
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


async def _send_network_pcm_with_recovery(
    sink: ClientSession,
    pcm: bytes,
    sample_rate: int,
) -> ClientSession:
    """Keep one reply pinned to the same NetworkSpeaker across reconnects.

    R5 added single-reconnect recovery. A real ESP32-C3 failure can reconnect
    and drop again during the resumed transfer, so a one-shot recovery still
    truncates the reply. This loop permits a small bounded number of reconnects
    while preserving the no-local-fallback rule.
    """
    current = sink
    remaining_pcm = pcm
    confirmed_global = 0
    reconnects_used = 0

    while True:
        try:
            await send_pcm_for_playback(
                current,
                remaining_pcm,
                sample_rate,
                emit_state=False,
            )
            return current
        except Exception as exc:
            attempt_segments = split_pcm_for_device(
                remaining_pcm,
                sample_rate,
                max_bytes=_tts_segment_max_bytes_for_session(current),
            )
            completed_attempt = max(
                0,
                min(
                    int(current.playback_completed_segments or 0),
                    len(attempt_segments),
                ),
            )
            confirmed_global += completed_attempt
            remaining_pcm = b"".join(attempt_segments[completed_attempt:])
            reconnects_used += 1

            print(
                f"[AUDIO-ROUTE-HOLD] speaker={current.device_id} disconnected; "
                f"keep_sink=network confirmed_total={confirmed_global} "
                f"reconnect={reconnects_used}/{AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS} "
                f"grace={AUDIO_ROUTE_RECONNECT_GRACE_SEC:.1f}s "
                f"error={type(exc).__name__}: {exc}"
            )

            if reconnects_used > AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS:
                print(
                    f"[AUDIO-ROUTE-ERROR] speaker={current.device_id} exceeded "
                    f"reconnect limit={AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS}; "
                    "local fallback suppressed to prevent mid-reply speaker switching"
                )
                raise RuntimeError(
                    f"NetworkSpeaker {current.device_id} repeatedly disconnected "
                    "during playback; local fallback suppressed"
                ) from exc

            replacement = await _wait_for_bound_speaker_reconnect(
                device_id=current.device_id,
                parent_device_id=current.parent_device_id,
                previous=current,
                timeout=AUDIO_ROUTE_RECONNECT_GRACE_SEC,
            )
            if replacement is None:
                print(
                    f"[AUDIO-ROUTE-ERROR] speaker={current.device_id} unavailable "
                    f"after {AUDIO_ROUTE_RECONNECT_GRACE_SEC:.1f}s; "
                    "local fallback suppressed to prevent mid-reply speaker switching"
                )
                raise RuntimeError(
                    f"NetworkSpeaker {current.device_id} disconnected during playback; "
                    "local fallback suppressed"
                ) from exc

            current = replacement
            if not remaining_pcm:
                print(
                    f"[AUDIO-ROUTE-RECOVER] speaker={current.device_id} reconnected "
                    "after all segments were already confirmed"
                )
                return current

            print(
                f"[AUDIO-ROUTE-RECOVER] speaker={current.device_id} reconnected; "
                f"resume_after_confirmed={confirmed_global} "
                f"reconnect={reconnects_used}/{AUDIO_ROUTE_RECONNECT_MAX_ATTEMPTS}"
            )


async def send_pcm_to_routed_sink(
    source: ClientSession,
    pcm: bytes,
    sample_rate: int,
) -> ClientSession:
    """Play one reply according to the companion's audio-output policy.

    HomeAgent may use either its local speaker or a bound NetworkSpeaker.
    HomeAgentMini is NetworkSpeaker-only and never falls back to local audio.
    """
    sink = _select_audio_sink(source)
    mode = _audio_output_mode_for_device(source.device_id)

    if sink is source:
        if source.device_id == HOMEAI_MINI_DEVICE_ID:
            raise RuntimeError(
                "HomeAgentMini requires a bound NetworkSpeaker; local playback is disabled"
            )
        await send_pcm_for_playback(source, pcm, sample_rate)
        return source

    print(
        f"[AUDIO-ROUTE] source={source.device_id} mode={mode} "
        f"-> speaker={sink.device_id} priority={sink.audio_priority}"
    )
    await send_state(source.ws, "speaking")

    strict_network = (
        source.device_id == HOMEAI_MINI_DEVICE_ID
        or mode == "network"
    )
    if strict_network:
        return await _send_network_pcm_with_recovery(
            sink,
            pcm,
            sample_rate,
        )

    try:
        await send_pcm_for_playback(
            sink,
            pcm,
            sample_rate,
            emit_state=False,
        )
        return sink
    except Exception as exc:
        # Automatic routing for future/unknown companions keeps the legacy
        # availability fallback. Explicit `network` mode is sticky above.
        print(
            f"[AUDIO-ROUTE-WARN] speaker={sink.device_id} failed in auto mode; "
            f"fallback={source.device_id}: {type(exc).__name__}: {exc}"
        )
        await send_pcm_for_playback(source, pcm, sample_rate)
        return source


def _reminder_session_available(session: ClientSession) -> bool:
    if not _is_primary_companion_session(session):
        return False
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
        sink = await send_pcm_to_routed_sink(session, pcm, sample_rate)
        print(
            f"[NOTIFY] device playback ACK id={record.reminder_id} "
            f"sink={sink.device_id}"
        )

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


def _is_kitchen_related_utterance(transcript: str, *, local_command: str = "") -> bool:
    if str(local_command or "").startswith("kitchen"):
        return True
    text = str(transcript or "").strip()
    if not text:
        return False
    if any(token in text for token in (
        "厨房终端", "厨房屏", "今天的菜单", "今日菜单", "晚餐菜单", "菜谱", "购物清单",
        "烧菜顺序", "烹饪顺序", "计时", "定时", "下一步", "上一步", "返回菜单",
        "结束今日烹饪", "结束今天的烹饪", "今天做完了", "私房菜", "保存菜谱",
    )):
        return True
    if KITCHEN_CURRENT_MENU is not None:
        compact = re.sub(r"[\s，。！？、,.!?]", "", text)
        for recipe in KITCHEN_CURRENT_MENU.get("recipes") or []:
            name = re.sub(r"[\s，。！？、,.!?]", "", str(recipe.get("name") or ""))
            if name and name in compact:
                return True
            for width in (4, 3, 2):
                if len(name) >= width and name[-width:] in compact:
                    return True
    active_recipe = KITCHEN_CURRENT_STATE.get("screen") == "recipe" and bool(KITCHEN_CURRENT_STATE.get("dish"))
    if not active_recipe:
        return False
    deictic = any(token in text for token in ("这个", "这道菜", "这一步", "现在这个", "当前这个"))
    cooking = any(token in text for token in (
        "多久", "怎么做", "怎么烧", "怎么煮", "怎么炒", "怎么烤", "怎么炖", "火候", "大火", "小火",
        "中火", "几分钟", "几秒", "熟", "咸", "淡", "调味", "加盐", "放多少", "食材", "步骤",
    ))
    return deictic or cooking


async def process_utterance(session: ClientSession) -> None:
    if session.processing:
        return
    session.processing = True
    pending_glass2_target: bool | None = None
    try:
        pcm = bytes(session.audio)
        if len(pcm) < 640:  # < 20 ms
            raise RuntimeError("recording too short")

        wav_bytes = pcm_to_wav_bytes(pcm)
        _best_effort_write_bytes(HOMEAI_DEBUG_DIR / "latest_input.wav", wav_bytes)

        safe_mode = (
            session.diag_glass_mode.lower()
            .replace("glass ", "")
            .replace(" ", "_")
            .replace("/", "_")
        )
        mode_path = HOMEAI_DEBUG_DIR / f"latest_input_{safe_mode}.wav"
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
            # Loopback acceptance mode still respects the product audio route.
            await send_pcm_to_routed_sink(session, pcm, MIC_RATE)
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
        _best_effort_write_text(HOMEAI_DEBUG_DIR / "latest_transcript.txt", transcript + "\n")

        await send_json(session.ws, {"type": "asr.result", "text": transcript})

        agent_started = time.perf_counter()
        answer = await _apply_audio_output_voice_command(session, transcript)
        local_command = "audio-route" if answer is not None else ""
        if answer is None:
            answer = await _apply_speaker_volume_voice_command(session, transcript)
            if answer is not None:
                local_command = "volume"
        if answer is None:
            glass2_result = await _apply_glass2_display_voice_command(session, transcript)
            if glass2_result is not None:
                answer, pending_glass2_target = glass2_result
                local_command = "glass2-display"
        if answer is None:
            answer = await _apply_kitchen_voice_command(session, transcript)
            if answer is not None:
                local_command = "kitchen"
        if answer is None:
            print(
                f"[STAGE] OpenClaw begin url={OPENCLAW_BASE_URL}/v1/chat/completions "
                f"model={OPENCLAW_MODEL} device={session.device_id} "
                f"user={session.openclaw_user}"
            )
            try:
                answer = await openclaw_chat(
                    transcript,
                    session.context,
                    openclaw_user=session.openclaw_user,
                )
                print("[STAGE] OpenClaw returned")
            except Exception as exc:
                fallback = _kitchen_offline_fallback(transcript) if _is_kitchen_related_utterance(transcript) else None
                if fallback is None:
                    raise
                answer = fallback
                local_command = "kitchen-offline"
                print(
                    f"[KITCHEN-OFFLINE] OpenClaw unavailable; local fallback used "
                    f"error={type(exc).__name__}: {exc}"
                )
        elif local_command == "audio-route":
            print(f"[AUDIO-ROUTE-VOICE] handled locally transcript={transcript!r}")
        elif local_command == "glass2-display":
            print(f"[GLASS2-VOICE] handled locally transcript={transcript!r}")
        elif local_command == "kitchen":
            print(f"[KITCHEN-VOICE] handled locally transcript={transcript!r}")
        elif local_command == "kitchen-offline":
            print(f"[KITCHEN-VOICE] OpenClaw offline fallback transcript={transcript!r}")
        else:
            print(f"[VOLUME-VOICE] handled locally transcript={transcript!r}")
        agent_ms = int((time.perf_counter() - agent_started) * 1000)
        print(f"[AGENT] answer_chars={len(answer)}")
        _vlog(f"[AGENT-TEXT] {answer}")
        _best_effort_write_text(HOMEAI_DEBUG_DIR / "latest_answer.txt", answer + "\n")
        await send_json(session.ws, {"type": "assistant.text", "text": answer})

        kitchen_audio = _is_kitchen_related_utterance(transcript, local_command=local_command)
        print(f"[STAGE] TTS begin provider={TTS_PROVIDER} target={'kitchen-ipad' if kitchen_audio else 'routed-sink'}")
        tts_started = time.perf_counter()
        pcm_out, sample_rate, tts_used = await synthesize_speech(answer)
        print("[STAGE] TTS returned")
        tts_ms = int((time.perf_counter() - tts_started) * 1000)
        wav_out = pcm_to_wav_bytes(pcm_out, sample_rate)
        _best_effort_write_bytes(HOMEAI_DEBUG_DIR / "latest_tts.wav", wav_out)

        total_ms = int((time.perf_counter() - turn_started) * 1000)
        print(f"[TTS:{tts_used}] {len(pcm_out)} bytes @ {sample_rate} Hz")
        print(
            f"[LATENCY] asr={asr_ms}ms agent={agent_ms}ms "
            f"tts={tts_ms}ms total_before_playback={total_ms}ms"
        )
        if kitchen_audio:
            event_id = "ka-" + uuid.uuid4().hex[:16]
            now = time.time()
            KITCHEN_AUDIO_CACHE[event_id] = wav_out
            KITCHEN_AUDIO_ORDER.append(event_id)
            while len(KITCHEN_AUDIO_ORDER) > KITCHEN_AUDIO_CACHE_MAX:
                old_id = KITCHEN_AUDIO_ORDER.pop(0)
                KITCHEN_AUDIO_CACHE.pop(old_id, None)
            KITCHEN_LATEST_AUDIO.update({
                "event_id": event_id, "text": answer, "kind": "assistant",
                "created_at": now, "expires_at": now + KITCHEN_AUDIO_TTL_SEC, "provider": tts_used, "source_id": "",
            })
            print(f"[KITCHEN-AUDIO] routed reply to iPad id={event_id} source={session.device_id}")
        else:
            sink = await send_pcm_to_routed_sink(session, pcm_out, sample_rate)
            print(f"[AUDIO-ROUTE] reply complete source={session.device_id} sink={sink.device_id}")
        session.processing = False
        await send_state(session.ws, "idle")
        if pending_glass2_target is not None:
            await _apply_glass2_target(session, pending_glass2_target)

    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        if HOMEAI_LOG_TRACEBACK:
            print("[TRACEBACK-BEGIN]")
            _log_traceback()
            print("[TRACEBACK-END]")
        try:
            await send_json(session.ws, {"type": "assistant.error", "message": str(exc)[:180]})
            await send_state(session.ws, "error")
            await asyncio.sleep(1.5)
            await send_state(session.ws, "idle")
        except Exception:
            pass
        session.processing = False
        if pending_glass2_target is not None:
            try:
                await _apply_glass2_target(session, pending_glass2_target)
            except Exception:
                pass



def _kitchen_clamp_timer_seconds(value: int | float) -> int:
    return max(KITCHEN_TIMER_MIN_SEC, min(int(round(value)), KITCHEN_TIMER_MAX_SEC))


def _kitchen_format_duration(seconds: int | float) -> str:
    sec = max(0, int(round(seconds)))
    minutes, rem = divmod(sec, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分{rem}秒" if rem else f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{rem}秒" if rem else f"{minutes}分钟"
    return f"{rem}秒"


def _kitchen_timer_remaining(timer: KitchenTimer, *, now: float | None = None) -> int:
    current = time.time() if now is None else now
    if timer.status == "running":
        return max(0, int(math.ceil(timer.ends_at - current)))
    if timer.status == "paused":
        return max(0, int(timer.paused_remaining_sec))
    return 0


def _kitchen_timer_public(timer: KitchenTimer) -> dict[str, Any]:
    return {
        "timer_id": timer.timer_id,
        "dish": timer.dish,
        "step": int(timer.step),
        "duration_sec": int(timer.duration_sec),
        "status": timer.status,
        "remaining_sec": _kitchen_timer_remaining(timer),
        "ends_at": float(timer.ends_at or 0.0),
        "created_at": float(timer.created_at),
        "updated_at": float(timer.updated_at),
        "finished_at": float(timer.finished_at or 0.0),
    }


def _kitchen_timer_snapshot() -> list[dict[str, Any]]:
    # Finished timers remain visible until acknowledged, but stale reminders
    # are eventually pruned so yesterday's "时间到" can never live forever.
    now = time.time()
    stale_ids = [
        t.timer_id for t in KITCHEN_TIMERS.values()
        if t.status == "finished" and t.finished_at and now - t.finished_at > KITCHEN_FINISHED_TIMER_TTL_SEC
    ]
    for timer_id in stale_ids:
        KITCHEN_TIMERS.pop(timer_id, None)
    if stale_ids:
        save_kitchen_timers()
    timers = list(KITCHEN_TIMERS.values())
    timers.sort(key=lambda t: (t.status == "finished", t.ends_at or 9e18, t.created_at))
    return [_kitchen_timer_public(t) for t in timers]


def save_kitchen_progress() -> bool:
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "progress": KITCHEN_RECIPE_PROGRESS}
        tmp = KITCHEN_PROGRESS_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(KITCHEN_PROGRESS_STATE_FILE)
        return True
    except Exception as exc:
        print(f"[KITCHEN-PROGRESS-WARN] save failed: {type(exc).__name__}: {exc}")
        return False


def load_kitchen_progress() -> None:
    KITCHEN_RECIPE_PROGRESS.clear()
    if not KITCHEN_PROGRESS_STATE_FILE.exists():
        return
    try:
        data = json.loads(KITCHEN_PROGRESS_STATE_FILE.read_text(encoding="utf-8"))
        raw = data.get("progress") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for date_text, dishes in raw.items():
                if not isinstance(dishes, dict):
                    continue
                clean: dict[str, int] = {}
                for dish, step in dishes.items():
                    try:
                        clean[str(dish)] = max(0, int(step))
                    except (TypeError, ValueError):
                        continue
                if clean:
                    KITCHEN_RECIPE_PROGRESS[str(date_text)] = clean
        print(f"[KITCHEN-PROGRESS] loaded dates={len(KITCHEN_RECIPE_PROGRESS)} file={KITCHEN_PROGRESS_STATE_FILE}")
    except Exception as exc:
        print(f"[KITCHEN-PROGRESS-WARN] load failed: {type(exc).__name__}: {exc}")


def _kitchen_progress_get(date_text: str, dish: str, total_steps: int) -> tuple[int, bool]:
    dishes = KITCHEN_RECIPE_PROGRESS.get(str(date_text), {})
    if str(dish) not in dishes:
        return 0, False
    step = max(0, min(int(dishes.get(str(dish), 0)), max(0, int(total_steps) - 1)))
    return step, True


def _kitchen_progress_set(date_text: str, dish: str, step: int, total_steps: int) -> int:
    date_key, dish_key = str(date_text), str(dish)
    value = max(0, min(int(step), max(0, int(total_steps) - 1)))
    KITCHEN_RECIPE_PROGRESS.setdefault(date_key, {})[dish_key] = value
    # Keep the file bounded; old dinner progress has little operational value.
    if len(KITCHEN_RECIPE_PROGRESS) > 45:
        for old_date in sorted(KITCHEN_RECIPE_PROGRESS)[:-45]:
            KITCHEN_RECIPE_PROGRESS.pop(old_date, None)
    save_kitchen_progress()
    return value


def _kitchen_idle_payload(message: str = "等待逐光发送菜单") -> dict[str, Any]:
    return {
        "type": "kitchen.show_idle",
        "eyebrow": "HOME AI · 厨房",
        "title": "厨房终端",
        "message": message,
        "can_pull_today": True,
        "footer": "可以在 iPad 直接加载今日菜单，也可以对逐光说“显示今天的菜单”",
    }


def _kitchen_maybe_return_idle() -> bool:
    global KITCHEN_RETURN_IDLE_AT, KITCHEN_CURRENT_STATE
    if not KITCHEN_RETURN_IDLE_AT or time.time() < KITCHEN_RETURN_IDLE_AT:
        return False
    KITCHEN_RETURN_IDLE_AT = 0.0
    KITCHEN_CURRENT_STATE = {"screen": "idle", "date": "", "dish": "", "step": 0}
    _kitchen_set_current_view(_kitchen_idle_payload())
    print("[KITCHEN] end-of-day grace complete -> idle")
    return True


def save_kitchen_timers() -> bool:
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "saved_at": datetime.now().astimezone().isoformat(),
            "timers": [asdict(t) for t in KITCHEN_TIMERS.values()],
        }
        tmp = KITCHEN_TIMER_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(KITCHEN_TIMER_STATE_FILE)
        return True
    except OSError as exc:
        print(f"[KITCHEN-TIMER-WARN] save failed: {type(exc).__name__}: {exc}")
        return False


def load_kitchen_timers() -> None:
    KITCHEN_TIMERS.clear()
    if not KITCHEN_TIMER_STATE_FILE.exists():
        return
    try:
        data = json.loads(KITCHEN_TIMER_STATE_FILE.read_text(encoding="utf-8"))
        rows = data.get("timers") if isinstance(data, dict) else []
        now = time.time()
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            timer = KitchenTimer(
                timer_id=str(raw.get("timer_id") or ""),
                dish=str(raw.get("dish") or ""),
                step=max(0, int(raw.get("step") or 0)),
                duration_sec=_kitchen_clamp_timer_seconds(int(raw.get("duration_sec") or KITCHEN_TIMER_MIN_SEC)),
                status=str(raw.get("status") or "running"),
                started_at=float(raw.get("started_at") or 0.0),
                ends_at=float(raw.get("ends_at") or 0.0),
                paused_remaining_sec=max(0, int(raw.get("paused_remaining_sec") or 0)),
                created_at=float(raw.get("created_at") or now),
                updated_at=float(raw.get("updated_at") or now),
                finished_at=float(raw.get("finished_at") or 0.0),
                notified=bool(raw.get("notified", False)),
            )
            if not timer.timer_id or not timer.dish:
                continue
            if timer.status not in {"running", "paused", "finished"}:
                timer.status = "running"
            # Old finished timers aren't useful after a day; prune them.
            if timer.status == "finished" and timer.finished_at and now - timer.finished_at > 86400:
                continue
            KITCHEN_TIMERS[timer.timer_id] = timer
        print(f"[KITCHEN-TIMER] loaded count={len(KITCHEN_TIMERS)} file={KITCHEN_TIMER_STATE_FILE}")
    except Exception as exc:
        print(f"[KITCHEN-TIMER-WARN] load failed: {type(exc).__name__}: {exc}")


def _kitchen_current_recipe() -> tuple[dict[str, Any] | None, int]:
    menu = KITCHEN_CURRENT_MENU
    dish = str(KITCHEN_CURRENT_STATE.get("dish") or "")
    step = max(0, int(KITCHEN_CURRENT_STATE.get("step") or 0))
    if menu is None or not dish:
        return None, step
    return recipe_for(menu, dish), step


def _kitchen_step_timer_hint(recipe: dict[str, Any] | None, step: int) -> dict[str, Any] | None:
    if recipe is None:
        return None
    hints = recipe.get("step_timers") or []
    if 0 <= step < len(hints) and isinstance(hints[step], dict):
        hint = dict(hints[step])
        try:
            hint["default_sec"] = _kitchen_clamp_timer_seconds(int(hint.get("default_sec") or 0))
            hint["max_sec"] = _kitchen_clamp_timer_seconds(int(hint.get("max_sec") or hint["default_sec"]))
        except (TypeError, ValueError):
            return None
        return hint
    return None


def _kitchen_timer_for_step(dish: str, step: int) -> KitchenTimer | None:
    candidates = [
        t for t in KITCHEN_TIMERS.values()
        if t.dish == dish and int(t.step) == int(step)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda t: t.created_at, reverse=True)
    return candidates[0]


def _kitchen_pick_timer(text: str = "") -> KitchenTimer | None:
    query = re.sub(r"[\s，。！？、,.!?]", "", text or "")
    recipe, step = _kitchen_current_recipe()
    if recipe is not None:
        current = _kitchen_timer_for_step(str(recipe.get("name") or ""), step)
        if current is not None:
            return current
    if query:
        matches = []
        for timer in KITCHEN_TIMERS.values():
            dish_compact = re.sub(r"[\s，。！？、,.!?]", "", timer.dish)
            if dish_compact and (dish_compact in query or any(part and part in query for part in re.split(r"[·（）()]+", dish_compact))):
                matches.append(timer)
        if matches:
            matches.sort(key=lambda t: t.created_at, reverse=True)
            return matches[0]
    active = [t for t in KITCHEN_TIMERS.values() if t.status in {"running", "paused"}]
    active.sort(key=lambda t: t.created_at, reverse=True)
    return active[0] if active else None


def _kitchen_timer_start(seconds: int | None = None) -> KitchenTimer:
    recipe, step = _kitchen_current_recipe()
    if recipe is None:
        raise KitchenMenuError("current screen is not a recipe step")
    hint = _kitchen_step_timer_hint(recipe, step)
    if seconds is None:
        if hint is None:
            raise KitchenMenuError("current step has no default timer")
        seconds = int(hint.get("default_sec") or 0)
    seconds = _kitchen_clamp_timer_seconds(seconds)
    dish = str(recipe.get("name") or "")
    old = _kitchen_timer_for_step(dish, step)
    if old is not None:
        KITCHEN_TIMERS.pop(old.timer_id, None)
    now = time.time()
    timer = KitchenTimer(
        timer_id="kt-" + uuid.uuid4().hex[:12],
        dish=dish,
        step=step,
        duration_sec=seconds,
        status="running",
        started_at=now,
        ends_at=now + seconds,
        created_at=now,
        updated_at=now,
    )
    KITCHEN_TIMERS[timer.timer_id] = timer
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] start id={timer.timer_id} dish={dish!r} step={step+1} sec={seconds}")
    return timer


def _kitchen_timer_set(timer: KitchenTimer, seconds: int) -> KitchenTimer:
    seconds = _kitchen_clamp_timer_seconds(seconds)
    now = time.time()
    timer.duration_sec = seconds
    timer.status = "running"
    timer.started_at = now
    timer.ends_at = now + seconds
    timer.paused_remaining_sec = 0
    timer.updated_at = now
    timer.finished_at = 0.0
    timer.notified = False
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] set id={timer.timer_id} sec={seconds}")
    return timer


def _kitchen_timer_adjust(timer: KitchenTimer, delta_sec: int) -> KitchenTimer:
    now = time.time()
    if timer.status == "finished":
        if delta_sec <= 0:
            return timer
        timer.status = "running"
        timer.duration_sec = _kitchen_clamp_timer_seconds(delta_sec)
        timer.started_at = now
        timer.ends_at = now + timer.duration_sec
        timer.finished_at = 0.0
        timer.notified = False
    elif timer.status == "paused":
        timer.paused_remaining_sec = _kitchen_clamp_timer_seconds(timer.paused_remaining_sec + delta_sec)
        timer.duration_sec = timer.paused_remaining_sec
    else:
        remaining = _kitchen_timer_remaining(timer, now=now)
        new_remaining = _kitchen_clamp_timer_seconds(remaining + delta_sec)
        timer.ends_at = now + new_remaining
        timer.duration_sec = new_remaining
    timer.updated_at = now
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] adjust id={timer.timer_id} delta={delta_sec} remaining={_kitchen_timer_remaining(timer)}")
    return timer


def _kitchen_timer_pause(timer: KitchenTimer) -> KitchenTimer:
    if timer.status != "running":
        return timer
    now = time.time()
    timer.paused_remaining_sec = _kitchen_timer_remaining(timer, now=now)
    timer.status = "paused"
    timer.updated_at = now
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] pause id={timer.timer_id} remaining={timer.paused_remaining_sec}")
    return timer


def _kitchen_timer_resume(timer: KitchenTimer) -> KitchenTimer:
    if timer.status != "paused":
        return timer
    now = time.time()
    remaining = _kitchen_clamp_timer_seconds(timer.paused_remaining_sec or timer.duration_sec)
    timer.status = "running"
    timer.started_at = now
    timer.ends_at = now + remaining
    timer.duration_sec = remaining
    timer.paused_remaining_sec = 0
    timer.updated_at = now
    timer.finished_at = 0.0
    timer.notified = False
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] resume id={timer.timer_id} remaining={remaining}")
    return timer


def _kitchen_clear_audio(*, event_id: str = "", source_id: str = "") -> None:
    current_event = str(KITCHEN_LATEST_AUDIO.get("event_id") or "")
    current_source = str(KITCHEN_LATEST_AUDIO.get("source_id") or "")
    if event_id and current_event != event_id:
        return
    if source_id and current_source != source_id:
        return
    KITCHEN_LATEST_AUDIO.update({
        "event_id": "", "text": "", "kind": "", "created_at": 0.0,
        "expires_at": 0.0, "provider": "", "source_id": "",
    })


def _kitchen_timer_remove(timer: KitchenTimer) -> None:
    KITCHEN_TIMERS.pop(timer.timer_id, None)
    _kitchen_clear_audio(source_id=timer.timer_id)
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] remove id={timer.timer_id}")


def _kitchen_clear_all_timers() -> int:
    count = len(KITCHEN_TIMERS)
    KITCHEN_TIMERS.clear()
    if str(KITCHEN_LATEST_AUDIO.get("kind") or "") == "timer":
        _kitchen_clear_audio()
    save_kitchen_timers()
    if count:
        print(f"[KITCHEN-TIMER] cleared all count={count}")
    return count


async def _kitchen_speak(text: str, *, kind: str = "assistant", source_id: str = "") -> dict[str, Any] | None:
    """Synthesize one KitchenTerminal voice event for playback on the iPad."""
    spoken = str(text or "").strip()
    if not spoken:
        return None
    try:
        pcm, sample_rate, provider = await synthesize_speech(spoken)
        wav = pcm_to_wav_bytes(pcm, sample_rate)
    except Exception as exc:
        print(f"[KITCHEN-AUDIO-ERROR] synthesize failed: {type(exc).__name__}: {exc}")
        return None

    event_id = "ka-" + uuid.uuid4().hex[:16]
    now = time.time()
    KITCHEN_AUDIO_CACHE[event_id] = wav
    KITCHEN_AUDIO_ORDER.append(event_id)
    while len(KITCHEN_AUDIO_ORDER) > KITCHEN_AUDIO_CACHE_MAX:
        old = KITCHEN_AUDIO_ORDER.pop(0)
        KITCHEN_AUDIO_CACHE.pop(old, None)

    KITCHEN_LATEST_AUDIO.update({
        "event_id": event_id,
        "text": spoken,
        "kind": kind,
        "created_at": now,
        "expires_at": now + KITCHEN_AUDIO_TTL_SEC,
        "provider": provider,
        "source_id": source_id,
    })
    print(f"[KITCHEN-AUDIO] queued id={event_id} kind={kind} provider={provider} text={spoken!r}")
    return _kitchen_audio_public()


def _kitchen_audio_public() -> dict[str, Any] | None:
    event_id = str(KITCHEN_LATEST_AUDIO.get("event_id") or "")
    expires_at = float(KITCHEN_LATEST_AUDIO.get("expires_at") or 0.0)
    if not event_id or event_id not in KITCHEN_AUDIO_CACHE or expires_at <= time.time():
        return None
    return {
        "event_id": event_id,
        "text": str(KITCHEN_LATEST_AUDIO.get("text") or ""),
        "kind": str(KITCHEN_LATEST_AUDIO.get("kind") or "assistant"),
        "created_at": float(KITCHEN_LATEST_AUDIO.get("created_at") or 0.0),
        "expires_at": expires_at,
        "provider": str(KITCHEN_LATEST_AUDIO.get("provider") or ""),
        "source_id": str(KITCHEN_LATEST_AUDIO.get("source_id") or ""),
        "url": f"{KITCHEN_HTTP_PATH}/audio?id={event_id}",
    }


async def _kitchen_timer_alert(timer: KitchenTimer) -> bool:
    text = f"{timer.dish}第{timer.step + 1}步计时结束，可以检查一下状态了。"
    event = await _kitchen_speak(text, kind="timer", source_id=timer.timer_id)
    if event is None:
        return False
    print(f"[KITCHEN-TIMER] iPad alert queued id={timer.timer_id} audio={event.get('event_id')}")
    return True


async def kitchen_timer_loop() -> None:
    while True:
        try:
            now = time.time()
            dirty = False
            for timer in list(KITCHEN_TIMERS.values()):
                if timer.status == "running" and timer.ends_at <= now:
                    timer.status = "finished"
                    timer.finished_at = now
                    timer.updated_at = now
                    dirty = True
                    print(f"[KITCHEN-TIMER] finished id={timer.timer_id} dish={timer.dish!r} step={timer.step+1}")
                if timer.status == "finished" and not timer.notified:
                    if await _kitchen_timer_alert(timer):
                        timer.notified = True
                        timer.updated_at = now
                        dirty = True
            if dirty:
                save_kitchen_timers()
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[KITCHEN-TIMER-ERROR] loop: {type(exc).__name__}: {exc}")
            await asyncio.sleep(1.0)


def _kitchen_voice_number(text: str) -> int | None:
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text in digits:
        return digits[text]
    if "十" in text:
        left, right = text.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _kitchen_duration_from_voice(text: str) -> int | None:
    total = 0
    found = False
    for pattern, mult in [
        (r"([0-9零〇一二两三四五六七八九十]+)\s*(?:小时|时)", 3600),
        (r"([0-9零〇一二两三四五六七八九十]+)\s*(?:分钟|分)", 60),
        (r"([0-9零〇一二两三四五六七八九十]+)\s*秒", 1),
    ]:
        for m in re.finditer(pattern, text):
            value = _kitchen_voice_number(m.group(1))
            if value is not None:
                total += value * mult
                found = True
    return _kitchen_clamp_timer_seconds(total) if found and total > 0 else None


def _kitchen_safe_filename(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', str(name or '').strip())
    value = value.strip(' .')
    return value[:80] or '私房菜'


def _kitchen_human_timer_text(hint: dict[str, Any] | None) -> str:
    if not isinstance(hint, dict):
        return ''
    default = int(hint.get('default_sec') or 0)
    maximum = int(hint.get('max_sec') or default)
    if default <= 0:
        return ''
    def short(sec: int) -> str:
        if sec % 3600 == 0 and sec >= 3600:
            return f'{sec // 3600}小时'
        if sec % 60 == 0 and sec >= 60:
            return f'{sec // 60}分钟'
        return f'{sec}秒'
    base = short(default)
    return f'{base}，可延长至{short(maximum)}' if maximum > default else base


def _kitchen_private_recipe_markdown(menu: dict[str, Any], recipe: dict[str, Any]) -> str:
    name = str(recipe.get('name') or '未命名菜谱')
    source_date = str(menu.get('date') or _kitchen_today())
    saved_at = datetime.now().astimezone().isoformat(timespec='seconds')
    q = lambda v: json.dumps(str(v), ensure_ascii=False)
    lines = [
        '---', 'type: private-recipe', 'schema: kitchen-private-recipe-v1',
        f'name: {q(name)}', f'source_date: {q(source_date)}', f'saved_at: {q(saved_at)}',
        'tags:', '  - 私房菜', '  - KitchenTerminal', '---', '',
        f'# 🍳 私房菜 · {name}', '', f'> 来源：{source_date} 晚餐 · 由 KitchenTerminal 保存',
    ]
    meta = ' · '.join(x for x in [str(recipe.get('type_label') or ''), str(recipe.get('estimated_text') or '')] if x)
    if meta:
        lines += ['', f'> {meta}']
    ingredients = recipe.get('ingredients') or []
    if ingredients:
        lines += ['', '## 🧺 食材', ''] + [f'- {x}' for x in ingredients]
    seasoning = recipe.get('seasoning') or []
    if seasoning:
        lines += ['', '## 🧂 调味', ''] + [f'- {x}' for x in seasoning]
    steps = recipe.get('steps') or []
    hints = recipe.get('step_timers') or []
    if steps:
        lines += ['', '## 👨‍🍳 做法', '']
        for idx, step in enumerate(steps):
            lines.append(f'{idx + 1}. {step}')
            hint = hints[idx] if idx < len(hints) else None
            timer_text = _kitchen_human_timer_text(hint)
            if timer_text:
                lines.append(f'   - ⏱️ 计时：{timer_text}')
    key_points = recipe.get('key_points') or []
    if key_points:
        lines += ['', '## 💡 关键点', ''] + [f'- {x}' for x in key_points]
    lines += ['', '---', '', f'保存自：{source_date} · KitchenTerminal', '']
    return '\n'.join(lines)


def _kitchen_write_private_recipe(menu: dict[str, Any], recipe: dict[str, Any]) -> Path:
    KITCHEN_PRIVATE_RECIPE_DIR.mkdir(parents=True, exist_ok=True)
    name = str(recipe.get('name') or '未命名菜谱')
    source_date = str(menu.get('date') or _kitchen_today())
    stem = _kitchen_safe_filename(name)
    target = KITCHEN_PRIVATE_RECIPE_DIR / f'{stem}.md'
    if target.exists():
        target = KITCHEN_PRIVATE_RECIPE_DIR / f'{stem}_{source_date}.md'
        seq = 2
        while target.exists():
            target = KITCHEN_PRIVATE_RECIPE_DIR / f'{stem}_{source_date}_{seq}.md'
            seq += 1
    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(_kitchen_private_recipe_markdown(menu, recipe), encoding='utf-8')
    tmp.replace(target)
    print(f'[KITCHEN] private recipe saved dish={name!r} path={target}')
    return target


def _kitchen_finish_payload(menu: dict[str, Any]) -> dict[str, Any]:
    active = [t for t in KITCHEN_TIMERS.values() if t.status in {'running', 'paused'}]
    return {
        'type': 'kitchen.show_finish',
        'eyebrow': f"{menu.get('date','')} · 收尾", 'title': '结束今日烹饪',
        'message': (f'还有 {len(active)} 个计时器正在运行，确认结束后会全部取消。' if active else '确认今天的烹饪已经完成？'),
        'active_timers': len(active),
        'footer': '确认后可以选择要保存到 Obsidian 私房菜的菜谱',
    }


def _kitchen_save_private_payload(menu: dict[str, Any]) -> dict[str, Any]:
    return {
        'type': 'kitchen.show_save_private',
        'eyebrow': f"{menu.get('date','')} · 今日收尾", 'title': '保存到私房菜？',
        'message': '如果今天有特别满意的菜，可以选中保存到 Obsidian「私房菜」。',
        'items': [str(x.get('name') or '') for x in menu.get('items') or [] if str(x.get('name') or '')],
        'private_dir': str(KITCHEN_PRIVATE_RECIPE_DIR),
        'footer': '可以多选，也可以直接结束不保存',
    }


async def _kitchen_begin_finish(*, speak: bool = False) -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    KITCHEN_CURRENT_STATE = {'screen': 'finish', 'date': str(menu.get('date') or ''), 'dish': '', 'step': 0}
    delivered = await kitchen_broadcast(_kitchen_finish_payload(menu))
    if speak:
        await _kitchen_speak('确认结束今天的烹饪吗？确认后我会关闭今天的计时器，然后让你选择是否保存菜谱到私房菜。', kind='assistant')
    return delivered


async def _kitchen_confirm_finish(*, speak: bool = False) -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    _kitchen_clear_all_timers()
    KITCHEN_CURRENT_STATE = {'screen': 'save_private', 'date': str(menu.get('date') or ''), 'dish': '', 'step': 0}
    delivered = await kitchen_broadcast(_kitchen_save_private_payload(menu))
    if speak:
        await _kitchen_speak('今天有没有想保存到私房菜的菜谱？可以勾选菜名，也可以直接选择不保存。', kind='assistant')
    return delivered


async def _kitchen_finalize_day(selected_names: list[str] | None = None, *, speak: bool = False) -> tuple[int, list[str]]:
    global KITCHEN_CURRENT_MENU, KITCHEN_CURRENT_STATE, KITCHEN_RETURN_IDLE_AT
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    saved: list[str] = []
    for name in [str(x).strip() for x in (selected_names or []) if str(x).strip()]:
        recipe = recipe_for(menu, name) or match_recipe(menu, name)
        if recipe is None:
            continue
        _kitchen_write_private_recipe(menu, recipe)
        saved.append(str(recipe.get('name') or name))
    _kitchen_clear_all_timers()
    _kitchen_clear_audio()
    date_text = str(menu.get('date') or _kitchen_today())
    KITCHEN_CURRENT_MENU = None
    KITCHEN_CURRENT_STATE = {'screen': 'done', 'date': date_text, 'dish': '', 'step': 0}
    KITCHEN_RETURN_IDLE_AT = time.time() + KITCHEN_IDLE_RETURN_DELAY_SEC
    msg = ('已保存到私房菜：' + '、'.join(saved)) if saved else '今天没有保存新的私房菜。'
    delivered = await kitchen_broadcast({
        'type': 'kitchen.show_done', 'eyebrow': f'{date_text} · 已收尾', 'title': '今天辛苦了',
        'message': msg + ' 今日烹饪已经结束。',
        'return_idle_at': KITCHEN_RETURN_IDLE_AT,
        'footer': f'约 {max(1, round(KITCHEN_IDLE_RETURN_DELAY_SEC / 60))} 分钟后自动返回等待页面',
    })
    if speak:
        spoken = (('已经保存' + '、'.join(saved) + '到私房菜。') if saved else '') + '今天的烹饪已经结束，辛苦了。'
        await _kitchen_speak(spoken, kind='assistant')
    return delivered, saved


def _kitchen_html() -> bytes:
    # KitchenTerminal: HTTP polling is authoritative; WebSocket is an optional
    # fast path. Timers and Q&A recovery state are Gateway-owned.
    html = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>KitchenTerminal __KITCHEN_UI_VERSION__</title>
<style>
:root{color-scheme:light;--bg:#f4f1e8;--card:#fffdf7;--ink:#171717;--muted:#777267;--line:#d9d3c7;--accent:#1d6b47;--danger:#9c2f2f;--soft:#eee9dd}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;width:100%;height:100%;background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;color:var(--ink);overflow:hidden}button,input{font:inherit;color:inherit}button{touch-action:manipulation}
#app{height:100%;display:flex;flex-direction:column;padding:12px 14px 10px}header{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px}.brand{font-weight:760;font-size:21px}.version{font-size:12px;color:var(--muted);margin-left:7px}.status{font-size:13px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;gap:7px;flex-wrap:wrap}.dot{width:9px;height:9px;border-radius:50%;background:var(--danger)}.dot.online{background:var(--accent)}.status-pill{border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:700;white-space:nowrap}.status-pill.ready{border-color:#9bbbaa;color:var(--accent);background:#f8fbf9}.status-pill.wait{color:var(--muted)}.status-pill.bad{border-color:#d3aaaa;color:var(--danger);background:#fff8f8}.help-btn{appearance:none;border:1px solid var(--line);background:#fff;border-radius:999px;min-height:32px;padding:6px 12px;font-size:13px;font-weight:750;color:var(--ink)}
#timerStrip{display:none;gap:10px;overflow-x:auto;padding:7px 0 11px;white-space:nowrap}.timer-chip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:10px 16px;font-size:28px;line-height:1.05;display:inline-flex;gap:10px;align-items:center;font-weight:720}.timer-chip.running{border-color:#9bbbaa}.timer-chip.paused{border-color:#d3b776}.timer-chip.finished{border-color:#c88f8f;color:var(--danger);font-weight:700}.timer-chip{cursor:pointer}.timer-chip .chip-x{border:0;background:transparent;color:var(--danger);font-size:28px;line-height:1;padding:0 0 1px 4px}.finish-btn{border-color:#c9a1a1!important;color:var(--danger)!important}.choice-list{display:grid;gap:10px;margin-top:12px}.choice-row{display:flex;align-items:center;gap:12px;border:1px solid var(--line);background:#fff;border-radius:14px;padding:14px 16px;font-size:20px;font-weight:650}.choice-row input{width:24px;height:24px}.finish-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px}.finish-actions button{min-height:54px;border-radius:13px;border:1px solid var(--line);background:#fff;font-size:17px;font-weight:700}.finish-actions .primary{background:var(--accent);border-color:var(--accent);color:#fff}.finish-actions .danger{color:var(--danger);border-color:#c9a1a1}
main{flex:1;min-height:0;display:flex;justify-content:center}.panel{width:100%;max-width:1000px;height:100%;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:20px 24px;display:flex;flex-direction:column;min-height:0}.eyebrow{font-size:14px;color:var(--muted);margin-bottom:5px}.title{font-size:38px;font-weight:780;line-height:1.12;margin:0 0 10px}.message{font-size:21px;line-height:1.4;color:#3f3b34}.content{flex:1;min-height:0;overflow:auto;padding-bottom:4px}.menu{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}.menu button,.action{appearance:none;border:1px solid var(--line);background:#fff;border-radius:15px;padding:16px 18px;text-align:left;font-size:23px;font-weight:680;min-height:66px}.menu button{display:flex;flex-direction:column;gap:5px}.menu-progress{font-size:13px;color:var(--accent);font-weight:700}.idle-actions{display:flex;justify-content:center;margin-top:34px}.idle-actions button{appearance:none;border:2px solid var(--accent);background:var(--accent);color:#fff;border-radius:22px;min-height:108px;min-width:min(100%,420px);width:min(100%,420px);padding:18px 28px;font-size:32px;line-height:1.15;font-weight:820;letter-spacing:.5px;box-shadow:0 10px 24px rgba(29,107,71,.18)}.done-note{margin-top:18px;color:var(--muted);font-size:16px}.toolbar{display:flex;gap:10px;margin-top:14px}.toolbar .action{flex:1;text-align:center;font-size:17px;min-height:52px;padding:10px}.recipe-meta{font-size:16px;color:var(--muted);margin-bottom:10px}.step-card{border:1px solid var(--line);background:#fff;border-radius:18px;padding:20px;margin-top:5px}.step-label{font-size:15px;color:var(--accent);font-weight:700;margin-bottom:8px}.step-text{font-size:30px;line-height:1.4;font-weight:650}.tips{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}.tips h3{font-size:15px;margin:0 0 7px;color:var(--muted)}.tips ul{margin:0;padding-left:21px}.tips li{font-size:16px;line-height:1.4;margin:4px 0}
.step-timer{margin-top:16px;border:1px solid #bfd2c7;background:#f7fbf8;border-radius:17px;padding:14px}.step-timer.finished{border-color:#d7aaaa;background:#fff7f7}.timer-title{font-size:15px;color:var(--muted);font-weight:700}.timer-time{font-size:42px;line-height:1;font-variant-numeric:tabular-nums;font-weight:800;letter-spacing:1px;margin-top:3px}.timer-state{font-size:14px;color:var(--muted);margin-top:5px}.timer-controls{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}.timer-controls button{appearance:none;border:1px solid var(--line);background:#fff;border-radius:12px;min-height:46px;padding:8px;font-size:15px;font-weight:650}.timer-controls button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.timer-controls button.danger{color:var(--danger)}
.nav{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding-top:13px}.nav button{appearance:none;border:1px solid var(--line);background:#fff;border-radius:13px;padding:12px;font-size:17px;font-weight:650;min-height:50px}.nav button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}.list-group{margin:0 0 16px}.list-group h3{font-size:19px;margin:0 0 7px}.list-group ul,.timeline{margin:0;padding-left:23px}.list-group li,.timeline li{font-size:19px;line-height:1.45;margin:6px 0}.timeline li{margin:9px 0}footer{padding-top:8px;text-align:center;font-size:12px;color:var(--muted)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.32);display:none;align-items:center;justify-content:center;padding:18px;z-index:20}.modal.show{display:flex}.modal-card{width:min(430px,94vw);background:#fffdf7;border-radius:20px;border:1px solid var(--line);padding:20px}.modal-card h2{font-size:23px;margin:0 0 14px}.time-inputs{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center}.time-inputs input{width:100%;font-size:34px;text-align:center;border:1px solid var(--line);border-radius:13px;padding:10px;background:#fff}.time-inputs span{font-size:28px;font-weight:700}.modal-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}.modal-actions button{min-height:48px;border-radius:12px;border:1px solid var(--line);background:#fff;font-size:17px;font-weight:700}.modal-actions .primary{background:var(--accent);border-color:var(--accent);color:#fff}
.qa-dock{width:100%;max-width:1000px;margin:9px auto 0;border:1px solid var(--line);background:#fffdf7;border-radius:19px;padding:10px 12px;display:flex;align-items:center;gap:14px;min-height:92px}.qa-btn{appearance:none;border:2px solid var(--accent);background:var(--accent);color:#fff;border-radius:17px;min-width:230px;min-height:72px;padding:12px 22px;font-size:24px;font-weight:820;box-shadow:0 4px 14px rgba(29,107,71,.18)}.qa-btn.recording{background:var(--danger);border-color:var(--danger);box-shadow:0 4px 14px rgba(156,47,47,.18)}.qa-btn.busy{background:#706c63;border-color:#706c63;box-shadow:none}.qa-copy{flex:1;min-width:0}.qa-status{font-size:15px;font-weight:760;color:var(--accent)}.qa-transcript{font-size:14px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px}.qa-answer{font-size:16px;line-height:1.35;margin-top:4px;max-height:50px;overflow:auto}.qa-clear{appearance:none;border:0;background:transparent;color:var(--muted);font-size:25px;line-height:1;padding:6px}.help-list{margin:4px 0 0;padding-left:22px}.help-list li{font-size:16px;line-height:1.5;margin:8px 0}.help-note{font-size:14px;line-height:1.45;color:var(--muted);background:var(--soft);border-radius:12px;padding:10px 12px;margin-top:12px}
@media(max-width:700px){#app{padding:9px 10px 8px}header{align-items:flex-start}.brand{font-size:18px}.version{display:none}.status{gap:5px;max-width:68%;}.status-pill{padding:6px 8px;font-size:11px}.help-btn{padding:5px 10px;font-size:12px}.menu{grid-template-columns:1fr}.panel{padding:17px}.title{font-size:31px}.step-text{font-size:26px}.timer-time{font-size:37px}.toolbar{flex-direction:column}.nav{gap:7px}.nav button{font-size:15px;padding:9px}.timer-controls{grid-template-columns:1fr 1fr}.message{font-size:19px}.qa-dock{gap:9px;padding:8px;min-height:82px}.qa-btn{min-width:176px;min-height:64px;font-size:21px;padding:10px 14px}.qa-answer{font-size:15px}}
</style>
</head>
<body>
<div id="app">
<header><div><span class="brand">KitchenTerminal</span><span class="version">__KITCHEN_UI_VERSION__</span></div><div class="status"><span id="micState" class="status-pill wait">🎙 麦克风 检测中</span><span id="audioState" class="status-pill wait">🔊 语音 待激活</span><button id="helpBtn" class="help-btn">？ 帮助</button><span id="dot" class="dot"></span><span id="statusText">连接中</span></div></header>
<div id="timerStrip"></div>
<main><section class="panel"><div id="eyebrow" class="eyebrow">HOME AI · 厨房</div><h1 id="title" class="title">厨房终端</h1><div id="message" class="message">正在读取 Gateway…</div><div id="content" class="content"></div><div id="nav" class="nav" style="display:none"></div></section></main>
<div id="qaDock" class="qa-dock"><button id="qaBtn" type="button" class="qa-btn">🎙 问逐光</button><div class="qa-copy"><div id="qaStatus" class="qa-status">可以问做法、替代食材、火候和补救办法</div><div id="qaTranscript" class="qa-transcript"></div><div id="qaAnswer" class="qa-answer"></div></div><button id="qaClear" class="qa-clear" aria-label="清除回答">×</button></div>
<footer id="footer">KitchenTerminal __KITCHEN_UI_VERSION__ · 等待 Gateway</footer>
</div>
<div id="timerModal" class="modal"><div class="modal-card"><h2>设置计时</h2><div class="time-inputs"><input id="minInput" inputmode="numeric" pattern="[0-9]*" value="0"><span>:</span><input id="secInput" inputmode="numeric" pattern="[0-9]*" value="30"></div><div class="modal-actions"><button id="modalCancel">取消</button><button id="modalOK" class="primary">确定</button></div></div></div>
<div id="helpModal" class="modal"><div class="modal-card"><h2>厨房终端操作指南</h2><ol class="help-list"><li><strong>开始做饭：</strong>在等待页点“加载今日菜单”，选择菜品进入步骤。</li><li><strong>按步骤操作：</strong>用“上一步 / 下一步”切换；需要返回总菜单时点“返回菜单”。</li><li><strong>计时：</strong>步骤里出现建议计时后可直接开始，也可以增减时间；顶部会持续显示正在运行的计时器。</li><li><strong>问逐光：</strong>点底部的大按钮开始说话，再点一次结束。可以问火候、替代食材、做法原因和翻车补救。</li><li><strong>语音播报：</strong>首次触碰页面后会自动激活；回答和厨房提示都在这台 iPad 本地播放。</li><li><strong>结束烹饪：</strong>回到今日菜单后点“结束今日烹饪”，可选择把喜欢的菜保存到私房菜。</li></ol><div class="help-note">顶部状态只用于快速确认：麦克风、厨房语音和 Gateway 连接是否正常，不再放测试按钮。</div><div class="modal-actions" style="grid-template-columns:1fr"><button id="helpClose" class="primary">知道了</button></div></div></div>
<script>
(function(){
  var ws=null,retry=1000,httpOK=false,wsOK=false,lastRevision='',currentView=null,latestTimers=[],drafts={},modalTarget=null;
  var audioCtx=null,audioUnlocked=false,audioMuted=false,lastAudioId='',pendingAudio=null,audioPlaying=false;
  var qaRecording=false,qaBusy=false,qaStream=null,qaCtx=null,qaSource=null,qaProcessor=null,qaChunks=[],qaSampleRate=0,qaStartedAt=0,qaAutoStop=null,qaSocket=null;
  var kitchenProtocol='__KITCHEN_PROTOCOL__',qaMaxMs=__KITCHEN_QA_MAX_MS__;
  var deviceId='KitchenTerminal-iPadMini';
  try{var saved=localStorage.getItem('kitchen.device_id');if(saved){deviceId=saved;}else{deviceId='KitchenTerminal-iPadMini-'+String(Date.now()).slice(-6);localStorage.setItem('kitchen.device_id',deviceId);}}catch(e){}
  var $=function(id){return document.getElementById(id);};
  function setStatus(){var ok=httpOK||wsOK;$('dot').className='dot'+(ok?' online':'');$('statusText').textContent=wsOK?'实时连接':(httpOK?'已连接':'正在重连');}
  function clearView(){$('content').innerHTML='';$('nav').innerHTML='';$('nav').style.display='none';$('nav').style.gridTemplateColumns='1fr 1fr 1fr';}
  function el(tag,cls,text){var x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined&&text!==null)x.textContent=String(text);return x;}
  function xhrGet(url,cb){var x=new XMLHttpRequest();x.open('GET',url+(url.indexOf('?')>=0?'&':'?')+'_='+Date.now(),true);x.onreadystatechange=function(){if(x.readyState!==4)return;if(x.status>=200&&x.status<300){httpOK=true;setStatus();cb(null,x.responseText);}else{httpOK=false;setStatus();cb(new Error('HTTP '+x.status),'');}};x.onerror=function(){httpOK=false;setStatus();cb(new Error('network'),'');};x.send(null);}
  function showError(text){$('message').textContent=text;$('footer').textContent='KitchenTerminal __KITCHEN_UI_VERSION__ · 页面错误';}
  function micStatusState(){var b=$('micState');if(!b)return;var secure=!!window.isSecureContext,gum=!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia);if(secure&&gum){b.textContent='🎙 麦克风 已就绪';b.className='status-pill ready';}else{b.textContent='🎙 麦克风 不可用';b.className='status-pill bad';}}
  function audioButtonState(){var b=$('audioState');if(!b)return;if(audioMuted){b.textContent='🔇 语音 已静音';b.className='status-pill bad';}else if(audioUnlocked){b.textContent='🔊 语音 已就绪';b.className='status-pill ready';}else{b.textContent='🔊 语音 待激活';b.className='status-pill wait';}}
  function unlockAudio(){try{var C=window.AudioContext||window.webkitAudioContext;if(!C)throw new Error('AudioContext unsupported');if(!audioCtx)audioCtx=new C();var p=audioCtx.resume();audioUnlocked=true;audioMuted=false;try{localStorage.setItem('kitchen.audio_enabled','1');localStorage.setItem('kitchen.audio_muted','0');}catch(e){}audioButtonState();if(p&&p.then){p.then(function(){if(pendingAudio)playKitchenAudio(pendingAudio);}).catch(function(){});}else if(pendingAudio){playKitchenAudio(pendingAudio);}}catch(e){var b=$('audioState');if(b){b.textContent='🔇 语音 不可用';b.className='status-pill bad';}showError('无法开启厨房语音：'+String(e));}}
  try{audioMuted=false;localStorage.setItem('kitchen.audio_muted','0');}catch(e){}micStatusState();audioButtonState();
  function opportunisticUnlock(){if(!audioUnlocked)unlockAudio();}
  document.addEventListener('click',opportunisticUnlock,false);
  function openHelp(){$('helpModal').className='modal show';}
  function closeHelp(){$('helpModal').className='modal';}
  $('helpBtn').onclick=openHelp;$('helpClose').onclick=closeHelp;
  function qaStateLabel(st){var m={idle:'可以提问',listening:'正在听…',uploading:'正在发送…',recognizing:'正在识别…',thinking:'正在回答…',speaking:'正在生成语音…',done:'回答完成',error:'出现问题'};return m[st]||'问逐光';}
  function qaRender(q){if(!q)return;var st=String(q.status||'idle'),updated=Number(q.updated_at||0),age=updated?((Date.now()/1000)-updated):0;var serverBusy=(st==='uploading'||st==='recognizing'||st==='thinking'||st==='speaking');if(serverBusy&&age>180){serverBusy=false;st='error';q.error='上一条问答状态已超时，已经自动解锁，可以重新提问。';qaBusy=false;}else{qaBusy=serverBusy;}if(!qaRecording){$('qaBtn').disabled=false;$('qaBtn').className='qa-btn'+(qaBusy?' busy':'');$('qaBtn').textContent=qaBusy?'处理中…':'🎙 问逐光';}$('qaStatus').textContent=qaRecording?'正在听…再次点击结束':qaStateLabel(st);$('qaTranscript').textContent=q.transcript?('你：'+q.transcript):'';$('qaAnswer').textContent=q.answer?('逐光：'+q.answer):(st==='error'?(q.error||'语音问答失败'):'');}
  function qaFloatConcat(parts){var total=0,i;for(i=0;i<parts.length;i++)total+=parts[i].length;var out=new Float32Array(total),p=0;for(i=0;i<parts.length;i++){out.set(parts[i],p);p+=parts[i].length;}return out;}
  function qaDownsample(input,inRate,outRate){if(!input||!input.length)return new Float32Array(0);if(inRate===outRate)return input;if(outRate>inRate)outRate=inRate;var ratio=inRate/outRate,newLen=Math.max(1,Math.round(input.length/ratio)),out=new Float32Array(newLen),offset=0;for(var i=0;i<newLen;i++){var next=Math.min(input.length,Math.round((i+1)*ratio)),sum=0,count=0;for(var j=offset;j<next;j++){sum+=input[j];count++;}out[i]=count?sum/count:0;offset=next;}return out;}
  function qaWav(samples,rate){var b=new ArrayBuffer(44+samples.length*2),v=new DataView(b);function ws(o,t){for(var i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i));}ws(0,'RIFF');v.setUint32(4,36+samples.length*2,true);ws(8,'WAVE');ws(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);ws(36,'data');v.setUint32(40,samples.length*2,true);var o=44;for(var i=0;i<samples.length;i++,o+=2){var x=Math.max(-1,Math.min(1,samples[i]));v.setInt16(o,x<0?x*32768:x*32767,true);}return b;}
  function qaStopTracks(stream){if(!stream)return;try{var tracks=stream.getTracks?stream.getTracks():[];for(var i=0;i<tracks.length;i++){try{tracks[i].stop();}catch(e){}}}catch(e){}}
  function qaCleanupCapture(){if(qaAutoStop){clearTimeout(qaAutoStop);qaAutoStop=null;}try{if(qaProcessor){qaProcessor.disconnect();qaProcessor.onaudioprocess=null;}}catch(e){}try{if(qaSource)qaSource.disconnect();}catch(e){}qaStopTracks(qaStream);qaStream=null;qaProcessor=null;qaSource=null;if(qaCtx){try{qaCtx.close();}catch(e){}}qaCtx=null;}
  function qaStart(){if(qaBusy){$('qaStatus').textContent='上一条问题还在处理中，请稍候…';return;}if(qaRecording){qaStop();return;}unlockAudio();var gum=navigator.mediaDevices&&navigator.mediaDevices.getUserMedia;if(!gum){qaRender({status:'error',error:'当前页面无法使用麦克风，请确认使用 HTTPS 地址。',updated_at:Date.now()/1000});return;}$('qaBtn').disabled=false;$('qaBtn').className='qa-btn busy';$('qaBtn').textContent='请求麦克风…';$('qaStatus').textContent='正在请求麦克风权限…';navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false}).then(function(stream){qaStream=stream;var C=window.AudioContext||window.webkitAudioContext;if(!C)throw new Error('AudioContext unavailable');qaCtx=new C();try{qaCtx.resume();}catch(e){}qaSampleRate=qaCtx.sampleRate||48000;qaSource=qaCtx.createMediaStreamSource(stream);qaProcessor=qaCtx.createScriptProcessor(4096,1,1);qaChunks=[];qaProcessor.onaudioprocess=function(ev){if(!qaRecording)return;var input=ev.inputBuffer.getChannelData(0);qaChunks.push(new Float32Array(input));};qaSource.connect(qaProcessor);qaProcessor.connect(qaCtx.destination);qaRecording=true;qaStartedAt=Date.now();$('qaBtn').disabled=false;$('qaBtn').className='qa-btn recording';$('qaBtn').textContent='⏹ 结束提问';$('qaStatus').textContent='正在听…再次点击结束';$('qaTranscript').textContent='';$('qaAnswer').textContent='';qaAutoStop=setTimeout(function(){if(qaRecording)qaStop();},qaMaxMs);}).catch(function(err){qaCleanupCapture();qaRecording=false;$('qaBtn').disabled=false;$('qaBtn').className='qa-btn';$('qaBtn').textContent='🎙 问逐光';qaRender({status:'error',error:'无法取得麦克风：'+String(err&&err.message?err.message:err),updated_at:Date.now()/1000});});}
  function qaStop(){if(!qaRecording)return;qaRecording=false;var duration=(Date.now()-qaStartedAt)/1000,parts=qaChunks.slice(),rate=qaSampleRate||48000;qaCleanupCapture();$('qaBtn').className='qa-btn busy';$('qaBtn').disabled=true;$('qaBtn').textContent='处理中…';if(duration<0.35||!parts.length){qaBusy=false;$('qaBtn').disabled=false;$('qaBtn').className='qa-btn';$('qaBtn').textContent='🎙 问逐光';qaRender({status:'error',error:'录音太短，请再说一次。',updated_at:Date.now()/1000});return;}var joined=qaFloatConcat(parts),down=qaDownsample(joined,rate,16000),wav=qaWav(down,16000);qaSend(wav);}
  function qaSend(wav){qaBusy=true;var scheme=(location.protocol==='https:')?'wss:':'ws:',rid='kq-'+String(Date.now())+'-'+Math.floor(Math.random()*10000);qaRender({status:'uploading',request_id:rid,updated_at:Date.now()/1000});try{qaSocket=new WebSocket(scheme+'//'+location.host+'/kitchen/ws');qaSocket.binaryType='arraybuffer';}catch(e){qaBusy=false;qaRender({status:'error',error:'无法建立语音上传连接',updated_at:Date.now()/1000});return;}var finished=false;qaSocket.onopen=function(){try{qaSocket.send(JSON.stringify({type:'kitchen.hello',protocol:kitchenProtocol,device_id:deviceId,capabilities:{touch:true,display:true,http_poll:true,timers:true,local_audio:true,mic_probe:true,qa_ptt:true}}));qaSocket.send(JSON.stringify({type:'kitchen.qa.start',request_id:rid,format:'audio/wav',sample_rate:16000}));qaSocket.send(wav);qaSocket.send(JSON.stringify({type:'kitchen.qa.stop',request_id:rid}));}catch(e){qaRender({status:'error',error:'发送录音失败',updated_at:Date.now()/1000});}};qaSocket.onmessage=function(ev){try{var m=JSON.parse(ev.data);if(m.qa)qaRender(m.qa);if(m.type==='kitchen.qa.transcript'&&m.text)$('qaTranscript').textContent='你：'+m.text;if(m.type==='kitchen.qa.answer'&&m.text)$('qaAnswer').textContent='逐光：'+m.text;if(m.type==='kitchen.qa.result'){finished=true;qaBusy=false;if(m.qa)qaRender(m.qa);if(m.audio)syncAudio(m.audio);try{qaSocket.close();}catch(e){}}if(m.type==='kitchen.qa.error'){finished=true;qaBusy=false;qaRender(m.qa||{status:'error',error:m.message||'语音问答失败',updated_at:Date.now()/1000});try{qaSocket.close();}catch(e){}}}catch(e){}};qaSocket.onerror=function(){};qaSocket.onclose=function(){qaSocket=null;if(!finished){/* HTTP polling is authoritative and can recover the final result. */}};}
  function qaTap(ev){if(ev){try{ev.preventDefault();}catch(e){}}qaStart();}if(window.PointerEvent){$('qaBtn').addEventListener('pointerup',qaTap,false);}else{$('qaBtn').addEventListener('click',qaTap,false);}$('qaClear').onclick=function(){$('qaTranscript').textContent='';$('qaAnswer').textContent='';action('qa_dismiss',{},function(){qaRender({status:'idle',updated_at:Date.now()/1000});});};
  function playKitchenAudio(a){if(!a||!a.event_id)return;if(a.event_id===lastAudioId)return;pendingAudio=a;if(!audioUnlocked||audioPlaying)return;if(Number(a.expires_at||0)>0&&Number(a.expires_at)<Date.now()/1000){lastAudioId=a.event_id;pendingAudio=null;return;}audioPlaying=true;fetch(a.url+'&_='+Date.now()).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.arrayBuffer();}).then(function(buf){return audioCtx.decodeAudioData(buf);}).then(function(decoded){var src=audioCtx.createBufferSource();src.buffer=decoded;src.connect(audioCtx.destination);src.onended=function(){audioPlaying=false;lastAudioId=a.event_id;pendingAudio=null;ackAudio(a.event_id);};src.start(0);}).catch(function(e){audioPlaying=false;console.log('kitchen audio failed',e);});}
  function syncAudio(a){if(!a||!a.event_id)return;if(a.event_id===lastAudioId)return;pendingAudio=a;playKitchenAudio(a);}
  function ackAudio(id){if(!id)return;xhrGet('/kitchen/action?action=audio_ack&value='+encodeURIComponent(id),function(){});}
  function action(name,params,cb){var url='/kitchen/action?action='+encodeURIComponent(name),k;params=params||{};for(k in params){if(params.hasOwnProperty(k)&&params[k]!==undefined&&params[k]!==null&&params[k]!==''){url+='&'+encodeURIComponent(k)+'='+encodeURIComponent(String(params[k]));}}xhrGet(url,function(err,text){if(err){if(cb)cb(err);return;}try{var r=JSON.parse(text);if(r&&r.timers)syncTimers(r.timers);if(r&&r.audio)syncAudio(r.audio);if(r&&r.qa)qaRender(r.qa);if(r&&r.view)render(r.view,true);if(cb)cb(null,r);}catch(e){showError('操作响应解析失败');if(cb)cb(e);}});}
  function navButton(text,name,primary){var b=el('button',primary?'primary':'',text);b.onclick=function(){action(name);};return b;}
  function fmt(sec){sec=Math.max(0,Math.ceil(Number(sec)||0));var m=Math.floor(sec/60),s=sec%60;return (m<10?'0':'')+m+':'+(s<10?'0':'')+s;}
  function remaining(t){if(!t)return 0;if(t.status==='running'&&t.ends_at){return Math.max(0,Math.ceil(Number(t.ends_at)-Date.now()/1000));}return Math.max(0,Number(t.remaining_sec)||0);}
  function timerForView(v){if(!v||v.type!=='kitchen.show_recipe')return null;for(var i=0;i<latestTimers.length;i++){var t=latestTimers[i];if(t.dish===v.title&&Number(t.step)===Number(v.step))return t;}return null;}
  function stepSize(sec){sec=Number(sec)||0;if(sec<=90)return 10;if(sec<=600)return 30;return 60;}
  function draftKey(v){return (v&&v.title?v.title:'')+'#'+String(v&&v.step!==undefined?v.step:0);}
  function suggested(v){var key=draftKey(v);if(drafts[key])return drafts[key];var hint=v&&v.timer_hint?v.timer_hint:null;var sec=hint&&hint.default_sec?Number(hint.default_sec):30;sec=Math.max(5,Math.min(5999,sec));drafts[key]=sec;return sec;}
  function setDraft(v,sec){sec=Math.max(5,Math.min(5999,Math.round(sec)));drafts[draftKey(v)]=sec;renderStepTimer(v);}
  function renderTimerStrip(){var strip=$('timerStrip');strip.innerHTML='';var shown=0;for(var i=0;i<latestTimers.length;i++){(function(t){if(t.status==='finished'&&currentView&&currentView.type==='kitchen.show_recipe'&&t.dish===currentView.title&&Number(t.step)===Number(currentView.step)){return;}var chip=el('div','timer-chip '+t.status);var label=t.dish+' · '+(Number(t.step)+1)+'步';var right=t.status==='finished'?'时间到':fmt(remaining(t));chip.appendChild(el('span','',label));chip.appendChild(el('strong','',right));chip.onclick=function(){action('timer_open',{timer_id:t.timer_id});};if(t.status==='finished'){var x=el('button','chip-x','×');x.setAttribute('aria-label','关闭提醒');x.onclick=function(ev){if(ev&&ev.stopPropagation)ev.stopPropagation();action('timer_dismiss',{timer_id:t.timer_id});};chip.appendChild(x);}strip.appendChild(chip);shown++;})(latestTimers[i]);}strip.style.display=shown?'flex':'none';}
  function syncTimers(timers){latestTimers=(timers&&timers.length!==undefined)?timers:[];renderTimerStrip();if(currentView&&currentView.type==='kitchen.show_recipe')renderStepTimer(currentView);}
  function timerButton(text,fn,cls){var b=el('button',cls||'',text);b.onclick=fn;return b;}
  function openModal(seconds,target){seconds=Math.max(5,Math.min(5999,Math.round(seconds||30)));$('minInput').value=Math.floor(seconds/60);$('secInput').value=seconds%60;modalTarget=target;$('timerModal').className='modal show';setTimeout(function(){try{$('minInput').focus();}catch(e){}},50);}
  function closeModal(){$('timerModal').className='modal';modalTarget=null;}
  $('modalCancel').onclick=closeModal;$('modalOK').onclick=function(){var m=parseInt($('minInput').value||'0',10)||0,s=parseInt($('secInput').value||'0',10)||0,total=m*60+s;total=Math.max(5,Math.min(5999,total));var target=modalTarget;closeModal();if(!target)return;if(target.timer){action('timer_set',{timer_id:target.timer.timer_id,seconds:total});}else if(target.view){setDraft(target.view,total);}};
  function renderStepTimer(v){var host=$('stepTimerHost');if(!host)return;host.innerHTML='';var hint=v.timer_hint||null,t=timerForView(v);if(!hint&&!t)return;var box=el('div','step-timer'+(t&&t.status==='finished'?' finished':''));var left=el('div');left.appendChild(el('div','timer-title',t?'本步骤计时':'建议计时'));var sec=t?remaining(t):suggested(v);var timeEl=el('div','timer-time',fmt(sec));timeEl.id='currentTimerTime';timeEl.onclick=function(){openModal(sec,t?{timer:t}:{view:v});};left.appendChild(timeEl);var state='';if(t){state=t.status==='running'?'计时中':(t.status==='paused'?'已暂停':'时间到');}else if(hint){state='默认 '+fmt(hint.default_sec)+(Number(hint.max_sec)>Number(hint.default_sec)?' · 可延长到 '+fmt(hint.max_sec):'');}left.appendChild(el('div','timer-state',state));box.appendChild(left);var controls=el('div','timer-controls');var step=stepSize(sec);
    if(!t){controls.appendChild(timerButton('－'+fmt(step),function(){setDraft(v,suggested(v)-step);}));controls.appendChild(timerButton('▶ 开始',function(){action('timer_start',{seconds:suggested(v)});},'primary'));controls.appendChild(timerButton('＋'+fmt(step),function(){setDraft(v,suggested(v)+step);}));controls.appendChild(timerButton('设置',function(){openModal(suggested(v),{view:v});}));}
    else if(t.status==='running'){controls.appendChild(timerButton('－'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:-step});}));controls.appendChild(timerButton('Ⅱ 暂停',function(){action('timer_pause',{timer_id:t.timer_id});},'primary'));controls.appendChild(timerButton('＋'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:step});}));controls.appendChild(timerButton('取消',function(){action('timer_cancel',{timer_id:t.timer_id});},'danger'));}
    else if(t.status==='paused'){controls.appendChild(timerButton('－'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:-step});}));controls.appendChild(timerButton('▶ 继续',function(){action('timer_resume',{timer_id:t.timer_id});},'primary'));controls.appendChild(timerButton('＋'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:step});}));controls.appendChild(timerButton('设置',function(){openModal(sec,{timer:t});}));}
    else{controls.appendChild(timerButton('＋'+fmt(step)+'继续',function(){action('timer_adjust',{timer_id:t.timer_id,seconds:step});},'primary'));controls.appendChild(timerButton('重新计时',function(){action('timer_start',{seconds:suggested(v)});}));controls.appendChild(timerButton('完成',function(){action('timer_dismiss',{timer_id:t.timer_id});}));}
    box.appendChild(controls);host.appendChild(box);
  }
  function tickTimers(){renderTimerStrip();if(currentView&&currentView.type==='kitchen.show_recipe'){var t=timerForView(currentView),n=$('currentTimerTime');if(t&&n)n.textContent=fmt(remaining(t));}}
  function render(v,force){
    if(!v||!v.type)return;var rev=String(v.revision||'');if(!force&&rev&&rev===lastRevision){currentView=v;return;}if(rev)lastRevision=rev;currentView=v;clearView();
    $('eyebrow').textContent=v.eyebrow||'HOME AI · 厨房';$('title').textContent=v.title||'厨房终端';$('message').textContent=v.message||'';$('footer').textContent=(v.footer||'')+' · __KITCHEN_UI_VERSION__';
    if(v.type==='kitchen.show_idle'){var ia=el('div','idle-actions');var pull=el('button','','加载今日菜单');pull.onclick=function(){action('today');};ia.appendChild(pull);$('content').appendChild(ia);return;}
    if(v.type==='kitchen.show_done'){var note=el('div','done-note','稍后会自动回到等待页面。');$('content').appendChild(note);return;}
    if(v.type==='kitchen.show_message')return;
    if(v.type==='kitchen.show_menu'){var grid=el('div','menu');var items=(v.items&&v.items.length!==undefined)?v.items:[];for(var i=0;i<items.length;i++){(function(index){var item=items[index];var label=(typeof item==='string')?item:((item&&item.name)?item.name:('菜品 '+(index+1)));var b=el('button','');b.appendChild(el('span','',label));if(item&&item.has_progress&&Number(item.total_steps)>0){b.appendChild(el('span','menu-progress','继续 · 第 '+(Number(item.progress_step)+1)+' / '+Number(item.total_steps)+' 步'));}b.onclick=function(){action('recipe',{value:label});};grid.appendChild(b);})(i);}$('content').appendChild(grid);var tools=el('div','toolbar');var shop=el('button','action','购物清单');shop.onclick=function(){action('shopping');};tools.appendChild(shop);var time=el('button','action','烧菜顺序');time.onclick=function(){action('timeline');};tools.appendChild(time);var finish=el('button','action finish-btn','结束今日烹饪');finish.onclick=function(){action('finish_start');};tools.appendChild(finish);$('content').appendChild(tools);return;}
    if(v.type==='kitchen.show_recipe'){$('message').textContent='';var metaText=(v.type_label||'菜谱')+(v.estimated_text?' · '+v.estimated_text:'');$('content').appendChild(el('div','recipe-meta',metaText));var card=el('div','step-card');var stepNum=(parseInt(v.step,10)||0)+1,total=parseInt(v.total_steps,10)||1;card.appendChild(el('div','step-label','步骤 '+stepNum+' / '+total));card.appendChild(el('div','step-text',v.step_text||'（本步骤内容为空）'));var timerHost=el('div','');timerHost.id='stepTimerHost';card.appendChild(timerHost);var tips=v.key_points||[];if(tips.length){var box=el('div','tips');box.appendChild(el('h3','','关键提醒'));var ul=el('ul');for(var j=0;j<tips.length;j++){ul.appendChild(el('li','',tips[j]));}box.appendChild(ul);card.appendChild(box);}$('content').appendChild(card);renderStepTimer(v);$('nav').style.display='grid';$('nav').appendChild(navButton('← 上一步','prev',false));$('nav').appendChild(navButton('返回菜单','menu',false));$('nav').appendChild(navButton('下一步 →','next',true));return;}
    if(v.type==='kitchen.show_finish'){var wrap=el('div','step-card');wrap.appendChild(el('div','step-label','今日收尾'));wrap.appendChild(el('div','step-text',v.message||'确认结束今天的烹饪？'));var fa=el('div','finish-actions');var back=el('button','','继续烹饪');back.onclick=function(){action('menu');};fa.appendChild(back);var yes=el('button','danger','确认结束');yes.onclick=function(){action('finish_confirm');};fa.appendChild(yes);wrap.appendChild(fa);$('content').appendChild(wrap);return;}
    if(v.type==='kitchen.show_save_private'){var items2=v.items||[],list=el('div','choice-list');for(var z=0;z<items2.length;z++){var row=el('label','choice-row');var ck=document.createElement('input');ck.type='checkbox';ck.value=items2[z];ck.className='private-choice';row.appendChild(ck);row.appendChild(el('span','',items2[z]));list.appendChild(row);}$('content').appendChild(list);var sa=el('div','finish-actions');var none=el('button','','不保存，直接结束');none.onclick=function(){action('finish_no_save');};sa.appendChild(none);var save=el('button','primary','保存所选并结束');save.onclick=function(){var picked=[],nodes=document.querySelectorAll('.private-choice:checked');for(var n=0;n<nodes.length;n++)picked.push(nodes[n].value);action('finish_save',{value:JSON.stringify(picked)});};sa.appendChild(save);$('content').appendChild(sa);return;}
    if(v.type==='kitchen.show_shopping'){var groups=v.groups||[];for(var g=0;g<groups.length;g++){var box2=el('section','list-group');box2.appendChild(el('h3','',groups[g].name||''));var ul2=el('ul'),gi=groups[g].items||[];for(var q=0;q<gi.length;q++){ul2.appendChild(el('li','',gi[q]));}box2.appendChild(ul2);$('content').appendChild(box2);}$('nav').style.display='grid';$('nav').style.gridTemplateColumns='1fr';$('nav').appendChild(navButton('返回今日菜单','menu',true));return;}
    if(v.type==='kitchen.show_timeline'){var ol=el('ol','timeline'),ti=v.items||[];for(var k=0;k<ti.length;k++){ol.appendChild(el('li','',ti[k]));}$('content').appendChild(ol);$('nav').style.display='grid';$('nav').style.gridTemplateColumns='1fr';$('nav').appendChild(navButton('返回今日菜单','menu',true));return;}
    showError('未知页面类型：'+v.type);
  }
  function poll(){xhrGet('/kitchen/view',function(err,text){if(err)return;try{var m=JSON.parse(text);if(m&&m.timers)syncTimers(m.timers);if(m&&m.audio)syncAudio(m.audio);if(m&&m.qa)qaRender(m.qa);if(m&&m.view)render(m.view,false);}catch(e){showError('Gateway 状态解析失败');}});}
  function connectWS(){var scheme=(location.protocol==='https:')?'wss:':'ws:';try{ws=new WebSocket(scheme+'//'+location.host+'/kitchen/ws');}catch(e){wsOK=false;setStatus();return;}ws.onopen=function(){retry=1000;wsOK=true;setStatus();try{ws.send(JSON.stringify({type:'kitchen.hello',protocol:kitchenProtocol,device_id:deviceId,capabilities:{touch:true,display:true,http_poll:true,timers:true,local_audio:true,mic_probe:true,qa_ptt:true}}));}catch(e){}};ws.onmessage=function(ev){try{var m=JSON.parse(ev.data);if(m.type==='kitchen.ready'){wsOK=true;setStatus();return;}if(m.type==='kitchen.sync'&&m.view){render(m.view,false);return;}if(m.type&&m.type.indexOf('kitchen.show_')===0){render(m,false);return;}}catch(e){}};ws.onclose=function(){wsOK=false;setStatus();setTimeout(connectWS,retry);retry=Math.min(Math.floor(retry*1.6),10000);};ws.onerror=function(){try{ws.close();}catch(e){}};}
  window.onerror=function(msg){showError('页面脚本错误：'+String(msg));return false;};
  poll();setInterval(poll,1000);setInterval(tickTimers,250);connectWS();
})();
</script>
</body>
</html>'''
    html = (
        html.replace("__KITCHEN_UI_VERSION__", KITCHEN_UI_VERSION)
        .replace("__KITCHEN_PROTOCOL__", KITCHEN_PROTOCOL)
        .replace("__KITCHEN_QA_MAX_MS__", str(KITCHEN_QA_MAX_SEC * 1000))
    )
    return html.encode("utf-8")


def _http_response(status: int, reason: str, body: bytes, content_type: str) -> Response:
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    headers["Pragma"] = "no-cache"
    headers["Expires"] = "0"
    headers["X-Content-Type-Options"] = "nosniff"
    return Response(status, reason, headers, body)


async def gateway_http_request(connection: Any, request: Any) -> Response | None:
    # Serve KitchenTerminal HTTP without creating a second web server.
    raw_path = str(getattr(request, "path", "") or "")
    parsed = urlsplit(raw_path)
    path = parsed.path
    query = parse_qs(parsed.query, keep_blank_values=True)
    if path in {WS_PATH, KITCHEN_WS_PATH, KITCHEN_CONTROL_PATH}:
        return None
    if path in {KITCHEN_HTTP_PATH, KITCHEN_HTTP_PATH + "/"}:
        return _http_response(200, "OK", _kitchen_html(), "text/html; charset=utf-8")
    if path == KITCHEN_HTTP_PATH + "/view":
        _kitchen_maybe_return_idle()
        body = json.dumps({
            "ok": True,
            "protocol": KITCHEN_PROTOCOL,
            "view": KITCHEN_CURRENT_VIEW,
            "state": KITCHEN_CURRENT_STATE,
            "timers": _kitchen_timer_snapshot(),
            "audio": _kitchen_audio_public(),
            "qa": _kitchen_qa_public(),
            "server_time": datetime.now().astimezone().isoformat(),
        }, ensure_ascii=False).encode("utf-8")
        return _http_response(200, "OK", body, "application/json; charset=utf-8")
    if path == KITCHEN_HTTP_PATH + "/audio":
        event_id = str((query.get("id") or [""])[0])
        wav = KITCHEN_AUDIO_CACHE.get(event_id)
        if wav is None:
            return _http_response(404, "Not Found", b"audio event not found", "text/plain; charset=utf-8")
        return _http_response(200, "OK", wav, "audio/wav")

    if path == KITCHEN_HTTP_PATH + "/action":
        action = str((query.get("action") or [""])[0]).strip().lower()
        action = {
            "show_today": "today", "menu.today": "today", "menu.load": "today", "pull_today": "today",
            "recipe.open": "recipe", "menu.recipe": "recipe",
            "step.next": "next", "recipe.next": "next", "step.prev": "prev", "recipe.prev": "prev",
            "menu.back": "menu", "show_menu": "menu",
            "menu.shopping": "shopping", "show_shopping": "shopping",
            "menu.timeline": "timeline", "show_timeline": "timeline",
            "day.finish": "finish_start", "day.finish.confirm": "finish_confirm",
            "day.finish.none": "finish_no_save", "day.finish.save": "finish_save",
            "qa.dismiss": "qa_dismiss", "qa.clear": "qa_dismiss",
        }.get(action, action)
        value = str((query.get("value") or [""])[0]).strip()
        timer_id = str((query.get("timer_id") or [""])[0]).strip()
        seconds_text = str((query.get("seconds") or [""])[0]).strip()
        try:
            if action == "today":
                await _kitchen_show_today_menu()
            elif action == "recipe":
                delivered, recipe = await _kitchen_open_recipe_by_name(value, None)
                if recipe is None:
                    raise KitchenMenuError(f"recipe not found: {value}")
            elif action == "next":
                await _kitchen_move_step(1)
            elif action == "prev":
                await _kitchen_move_step(-1)
            elif action == "menu":
                menu = KITCHEN_CURRENT_MENU or _kitchen_load()
                await _kitchen_show_menu(menu)
            elif action == "shopping":
                await _kitchen_show_shopping()
            elif action == "timeline":
                await _kitchen_show_timeline()
            elif action == "finish_start":
                await _kitchen_begin_finish(speak=True)
            elif action == "finish_confirm":
                await _kitchen_confirm_finish(speak=True)
            elif action == "finish_no_save":
                await _kitchen_finalize_day([], speak=True)
            elif action == "finish_save":
                try:
                    selected = json.loads(value) if value else []
                except json.JSONDecodeError as exc:
                    raise KitchenMenuError("invalid recipe selection") from exc
                if not isinstance(selected, list):
                    raise KitchenMenuError("invalid recipe selection")
                await _kitchen_finalize_day([str(x) for x in selected], speak=True)
            elif action == "timer_open":
                timer = KITCHEN_TIMERS.get(timer_id)
                if timer is None:
                    raise KitchenMenuError("timer not found")
                delivered, recipe = await _kitchen_open_recipe_by_name(timer.dish, timer.step)
                if recipe is None:
                    raise KitchenMenuError(f"recipe not found: {timer.dish}")
            elif action == "audio_ack":
                _kitchen_clear_audio(event_id=value)
            elif action == "timer_start":
                seconds = int(seconds_text) if seconds_text else None
                _kitchen_timer_start(seconds)
            elif action in {"timer_adjust", "timer_set", "timer_pause", "timer_resume", "timer_cancel", "timer_dismiss"}:
                timer = KITCHEN_TIMERS.get(timer_id) if timer_id else _kitchen_pick_timer()
                if timer is None:
                    raise KitchenMenuError("timer not found")
                if action == "timer_adjust":
                    _kitchen_timer_adjust(timer, int(seconds_text or "0"))
                elif action == "timer_set":
                    _kitchen_timer_set(timer, int(seconds_text or "0"))
                elif action == "timer_pause":
                    _kitchen_timer_pause(timer)
                elif action == "timer_resume":
                    _kitchen_timer_resume(timer)
                else:
                    _kitchen_timer_remove(timer)
            elif action == "qa_dismiss":
                _kitchen_qa_set("idle", reset=True)
            else:
                raise KitchenMenuError(f"unsupported action: {action}")
            payload = {"ok": True, "action": action, "view": KITCHEN_CURRENT_VIEW, "state": KITCHEN_CURRENT_STATE, "timers": _kitchen_timer_snapshot(), "audio": _kitchen_audio_public(), "qa": _kitchen_qa_public()}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return _http_response(200, "OK", body, "application/json; charset=utf-8")
        except (KitchenMenuError, ValueError, TypeError) as exc:
            body = json.dumps({"ok": False, "action": action, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
            return _http_response(400, "Bad Request", body, "application/json; charset=utf-8")
    if path == KITCHEN_HTTP_PATH + "/health":
        body = json.dumps({
            "ok": True,
            "protocol": KITCHEN_PROTOCOL,
            "clients": len(KITCHEN_SESSIONS),
            "current_view": str(KITCHEN_CURRENT_VIEW.get("type") or ""),
            "menu_dir": str(KITCHEN_MENU_DIR),
            "private_recipe_dir": str(KITCHEN_PRIVATE_RECIPE_DIR),
            "state": KITCHEN_CURRENT_STATE,
            "timers": _kitchen_timer_snapshot(),
            "audio": _kitchen_audio_public(),
            "qa": _kitchen_qa_public(),
        }, ensure_ascii=False).encode("utf-8")
        return _http_response(200, "OK", body, "application/json; charset=utf-8")
    if path == "/":
        body = ("HomeAIAgent Gateway\nKitchenTerminal: " + KITCHEN_HTTP_PATH + "\n").encode("utf-8")
        return _http_response(200, "OK", body, "text/plain; charset=utf-8")
    return _http_response(404, "Not Found", b"Not Found\n", "text/plain; charset=utf-8")

def _kitchen_set_current_view(payload: dict[str, Any]) -> dict[str, Any]:
    global KITCHEN_CURRENT_VIEW
    view = dict(payload)
    view["protocol"] = KITCHEN_PROTOCOL
    view["revision"] = str(view.get("revision") or f"k-{int(time.time() * 1000)}")
    KITCHEN_CURRENT_VIEW = view
    return view


async def kitchen_broadcast(payload: dict[str, Any]) -> int:
    view = _kitchen_set_current_view(payload)
    delivered = 0
    for session in list(KITCHEN_SESSIONS):
        try:
            await send_json(session.ws, view)
            delivered += 1
        except Exception:
            pass
    _vlog(f"[KITCHEN] broadcast type={view.get('type')} clients={delivered}")
    return delivered


def _kitchen_today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _kitchen_load(date_text: str | None = None) -> dict[str, Any]:
    global KITCHEN_CURRENT_MENU
    target = date_text or _kitchen_today()
    menu = load_kitchen_menu(KITCHEN_MENU_DIR, target)
    KITCHEN_CURRENT_MENU = menu
    print(
        f"[KITCHEN] menu loaded date={menu.get('date')} "
        f"dishes={len(menu.get('items') or [])} source={menu.get('source')}"
    )
    return menu


def _kitchen_menu_payload(menu: dict[str, Any]) -> dict[str, Any]:
    servings = menu.get("servings")
    minutes = menu.get("estimated_minutes")
    bits = []
    if servings:
        bits.append(f"{servings}人份")
    if minutes:
        bits.append(f"约{minutes}分钟")
    date_text = str(menu.get("date") or "")
    items: list[dict[str, Any]] = []
    for raw in menu.get("items") or []:
        item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
        name = str(item.get("name") or "")
        recipe = recipe_for(menu, name)
        total = len((recipe or {}).get("steps") or [])
        step, has_progress = _kitchen_progress_get(date_text, name, total) if total else (0, False)
        item["progress_step"] = step
        item["has_progress"] = has_progress
        item["total_steps"] = total
        items.append(item)
    return {
        "type": "kitchen.show_menu",
        "eyebrow": f"{menu.get('date','')} {menu.get('weekday','')} · {menu.get('style','家常')}",
        "title": "今日晚餐",
        "message": " · ".join(bits) if bits else "选择一道菜查看步骤",
        "items": items,
        "footer": "再次进入菜谱会自动继续上次看到的步骤",
    }


def _kitchen_recipe_payload(menu: dict[str, Any], recipe: dict[str, Any], step: int) -> dict[str, Any]:
    steps = recipe.get("steps") or []
    if not steps:
        raise KitchenMenuError(f"recipe has no steps: {recipe.get('name')}")
    step = max(0, min(int(step), len(steps) - 1))
    timer_hint = _kitchen_step_timer_hint(recipe, step)
    return {
        "type": "kitchen.show_recipe",
        "eyebrow": f"{menu.get('date','')} · 今日菜谱",
        "title": str(recipe.get("name") or "菜谱"),
        "type_label": str(recipe.get("type_label") or ""),
        "estimated_text": str(recipe.get("estimated_text") or ""),
        "step": step,
        "total_steps": len(steps),
        "step_text": str(steps[step]),
        "timer_hint": timer_hint,
        "key_points": recipe.get("key_points") or [],
        "footer": "语音可说：下一步 / 上一步 / 开始计时 / 还有多久",
    }


def _kitchen_shopping_payload(menu: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "kitchen.show_shopping",
        "eyebrow": f"{menu.get('date','')} · 采购",
        "title": "超市购物单",
        "message": "",
        "groups": menu.get("shopping") or [],
        "footer": "今日菜单购物清单",
    }


def _kitchen_timeline_payload(menu: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "kitchen.show_timeline",
        "eyebrow": f"{menu.get('date','')} · 烹饪安排",
        "title": "省事操作顺序",
        "message": "",
        "items": menu.get("timeline") or [],
        "footer": "按顺序做，减少厨房同时开战",
    }


async def _kitchen_show_menu(menu: dict[str, Any]) -> int:
    global KITCHEN_CURRENT_STATE
    KITCHEN_CURRENT_STATE = {
        "screen": "menu",
        "date": str(menu.get("date") or ""),
        "dish": "",
        "step": 0,
    }
    return await kitchen_broadcast(_kitchen_menu_payload(menu))


async def _kitchen_show_today_menu() -> tuple[int, dict[str, Any]]:
    menu = _kitchen_load()
    delivered = await _kitchen_show_menu(menu)
    return delivered, menu


async def _kitchen_open_recipe(recipe: dict[str, Any], step: int | None = None) -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU
    if menu is None:
        menu = _kitchen_load()
    steps = recipe.get("steps") or []
    if not steps:
        raise KitchenMenuError(f"recipe has no steps: {recipe.get('name')}")
    date_text = str(menu.get("date") or "")
    dish = str(recipe.get("name") or "")
    if step is None:
        step, _ = _kitchen_progress_get(date_text, dish, len(steps))
    step = max(0, min(int(step), len(steps) - 1))
    _kitchen_progress_set(date_text, dish, step, len(steps))
    # If a finished timer brought the cook back to exactly this step, opening
    # the page is the acknowledgement: don't let the top alert reappear later.
    finished = _kitchen_timer_for_step(dish, step)
    if finished is not None and finished.status == "finished":
        _kitchen_timer_remove(finished)
    KITCHEN_CURRENT_STATE = {
        "screen": "recipe",
        "date": date_text,
        "dish": dish,
        "step": step,
    }
    return await kitchen_broadcast(_kitchen_recipe_payload(menu, recipe, step))


async def _kitchen_open_recipe_by_name(name: str, step: int | None = None) -> tuple[int, dict[str, Any] | None]:
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_load()
    recipe = recipe_for(menu, name) or match_recipe(menu, name)
    if recipe is None:
        return 0, None
    delivered = await _kitchen_open_recipe(recipe, step)
    return delivered, recipe


async def _kitchen_show_shopping() -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_load()
    KITCHEN_CURRENT_STATE = {
        "screen": "shopping",
        "date": str(menu.get("date") or ""),
        "dish": "",
        "step": 0,
    }
    return await kitchen_broadcast(_kitchen_shopping_payload(menu))


async def _kitchen_show_timeline() -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_load()
    KITCHEN_CURRENT_STATE = {
        "screen": "timeline",
        "date": str(menu.get("date") or ""),
        "dish": "",
        "step": 0,
    }
    return await kitchen_broadcast(_kitchen_timeline_payload(menu))


async def _kitchen_move_step(delta: int) -> tuple[int, dict[str, Any] | None, int]:
    menu = KITCHEN_CURRENT_MENU
    dish = str(KITCHEN_CURRENT_STATE.get("dish") or "")
    if menu is None or not dish:
        return 0, None, 0
    recipe = recipe_for(menu, dish)
    if recipe is None:
        return 0, None, 0
    steps = recipe.get("steps") or []
    if not steps:
        return 0, recipe, 0
    current = int(KITCHEN_CURRENT_STATE.get("step") or 0)
    target = max(0, min(current + int(delta), len(steps) - 1))
    delivered = await _kitchen_open_recipe(recipe, target)
    return delivered, recipe, target


def _kitchen_recipe_context(recipe: dict[str, Any], *, step: int) -> str:
    steps = recipe.get("steps") or []
    lines = [f"当前菜品：{recipe.get('name','')}"]
    if recipe.get("estimated_text"):
        lines.append(f"预计用时：{recipe.get('estimated_text')}")
    if recipe.get("ingredients"):
        lines.append("食材：" + "；".join(str(x) for x in recipe.get("ingredients") or []))
    if recipe.get("seasoning"):
        lines.append("调味：" + "；".join(str(x) for x in recipe.get("seasoning") or []))
    if steps:
        step = max(0, min(step, len(steps) - 1))
        lines.append(f"当前步骤：第{step + 1}/{len(steps)}步，{steps[step]}")
        lines.append("完整步骤：" + "；".join(f"{i+1}.{x}" for i, x in enumerate(steps)))
    if recipe.get("key_points"):
        lines.append("关键点：" + "；".join(str(x) for x in recipe.get("key_points") or []))
    return "\n".join(lines)


def _kitchen_agent_context_text() -> str:
    menu = KITCHEN_CURRENT_MENU
    state = KITCHEN_CURRENT_STATE
    if menu is None:
        return "KitchenTerminal 当前没有已加载菜单。"
    items = "、".join(str(x.get("name") or "") for x in menu.get("items") or [])
    lines = [
        "KitchenTerminal 当前上下文：",
        f"- 日期：{menu.get('date','')}",
        f"- 今日菜单：{items}",
        f"- 当前屏幕：{state.get('screen','idle')}",
    ]
    dish = str(state.get("dish") or "")
    if dish:
        recipe = recipe_for(menu, dish)
        if recipe is not None:
            detail = _kitchen_recipe_context(recipe, step=int(state.get("step") or 0))
            lines.append(detail)
            lines.append("当用户说“这个 / 这道菜 / 现在这个”时，优先指当前 KitchenTerminal 菜品和步骤。")
    active_timers = [t for t in KITCHEN_TIMERS.values() if t.status in {"running", "paused"}]
    if active_timers:
        timer_bits = []
        for timer in sorted(active_timers, key=lambda x: x.created_at)[-6:]:
            timer_bits.append(
                f"{timer.dish}第{timer.step + 1}步:{timer.status},剩余{_kitchen_format_duration(_kitchen_timer_remaining(timer))}"
            )
        lines.append("厨房计时器：" + "；".join(timer_bits))
    lines.append("厨房指令集：外部 HomeAgent 使用“厨房”作为固定域前缀，例如“厨房显示今天菜单 / 厨房打开河虾 / 厨房结束今天烹饪”；KitchenTerminal 自身麦克风来源可省略“厨房”。显示/拉取/加载今日菜单；打开/继续某道菜；下一步/上一步/返回菜单；购物清单；烧菜顺序；开始/暂停/继续/增减/取消/查询计时；结束今日烹饪；保存私房菜。自然语言可按这些意图归一处理。")
    return "\n".join(lines)[:3500]


def _kitchen_voice_namespace(transcript: str) -> tuple[str, bool]:
    """Return text with wake-word/domain prefixes removed and whether 厨房 was explicit."""
    text = str(transcript or "").strip()
    text = re.sub(r"^逐光[，,、\s]*", "", text).strip()
    named = False
    m = re.match(r"^(?:请)?(?:在)?厨房(?:终端|屏)?[，,、：:\s]*", text)
    if m:
        named = True
        text = text[m.end():].strip()
    return text, named


def _kitchen_voice_source_is_terminal(session: ClientSession | None) -> bool:
    if session is None:
        return False
    return str(getattr(session, "device_id", "") or "").lower().startswith("kitchenterminal")


def _kitchen_voice_query_after_open(transcript: str) -> str:
    text, _ = _kitchen_voice_namespace(transcript)
    text = re.sub(r"^(?:请)?(?:在)?厨房终端", "", text)
    text = re.sub(r"^(?:帮我)?(?:打开|显示|看看|看一下|看)", "", text)
    text = re.sub(r"[。！？!?]+$", "", text).strip()
    return text


def _kitchen_fast_control_intent(transcript: str) -> str | None:
    """Classify deterministic KitchenTerminal controls without OpenClaw.

    This is deliberately conservative: only imperative/navigation commands live
    here. Recipe Q&A can still use OpenClaw when it is healthy.
    """
    text, _ = _kitchen_voice_namespace(transcript)
    compact = re.sub(r"[\s，。！？、,.!?：:；;]", "", text)

    today_exact = {
        "显示今天的菜单", "显示今天菜单", "显示今日菜单", "显示今天的菜谱", "显示今天菜谱",
        "打开今天的菜单", "打开今天菜单", "打开今日菜单", "打开今天的菜谱",
        "加载今天的菜单", "加载今天菜单", "加载今日菜单", "拉取今天的菜单", "拉取今天菜单",
        "调出今天的菜单", "调出今天菜单", "调出今日菜单",
        "推送菜单", "推送今天菜单", "推送今天的菜单", "推送今日菜单", "菜单推送",
        "发送菜单", "发送今天菜单", "发送今日菜单", "同步菜单", "同步今日菜单",
        "今天吃什么", "今天晚饭吃什么",
        "今天的菜单", "今日菜单", "今天菜单", "今天晚餐", "今日晚餐",
    }
    if compact in today_exact:
        return "menu.today"
    if (("今天" in compact or "今日" in compact) and any(x in compact for x in ("菜单", "菜谱", "晚餐", "晚饭"))
            and any(x in compact for x in ("显示", "打开", "加载", "拉取", "调出", "看看", "看下", "推送", "发送", "同步", "投送"))):
        return "menu.today"
    # With the explicit 厨房 namespace already stripped by _kitchen_voice_namespace(),
    # terse commands such as “厨房推送菜单” should stay entirely local and must
    # never fall through to OpenClaw.
    if any(x in compact for x in ("菜单", "菜谱")) and any(x in compact for x in ("推送", "发送", "同步", "投送")):
        return "menu.today"

    if "购物清单" in compact and any(x in compact for x in ("显示", "打开", "看", "给我", "调出", "加载")):
        return "menu.shopping"
    if any(x in compact for x in ("烧菜顺序", "烹饪顺序", "做菜顺序", "操作时间线")):
        return "menu.timeline"
    if compact in {"下一步", "下一页", "继续", "接下来", "然后呢", "往下", "继续下一步", "接下来怎么做", "下一步怎么做"}:
        return "step.next"
    if compact in {"上一步", "上一页", "返回上一步", "往回一步", "退一步", "刚才那一步"}:
        return "step.prev"
    if compact in {"返回菜单", "回到菜单", "回菜单", "回到今日菜单", "回到今天的菜单", "看菜单"}:
        return "menu.back"
    if any(x in compact for x in ("结束今天的烹饪", "结束今日烹饪", "今天做完了", "今天烧完了", "今天就到这里", "结束烹饪")):
        return "day.finish"
    return None


def _kitchen_offline_fallback(transcript: str) -> str | None:
    """Best-effort KitchenTerminal answer when OpenClaw is temporarily down."""
    text = str(transcript or "").strip()
    recipe, step = _kitchen_current_recipe()
    if recipe is None:
        return None
    steps = recipe.get("steps") or []
    if not steps:
        return None
    step = max(0, min(int(step), len(steps) - 1))
    dish = str(recipe.get("name") or "当前菜谱")
    step_text = str(steps[step]).strip()
    hint = _kitchen_step_timer_hint(recipe, step)

    if any(x in text for x in ("多久", "多长时间", "几分钟", "几秒", "时间")) and hint:
        default_sec = int(hint.get("default_sec") or 0)
        max_sec = int(hint.get("max_sec") or default_sec)
        if max_sec > default_sec:
            return (f"逐光暂时无法连接 OpenClaw。{dish}第{step + 1}步建议先计时"
                    f"{_kitchen_format_duration(default_sec)}，需要时可延长到{_kitchen_format_duration(max_sec)}。")
        return f"逐光暂时无法连接 OpenClaw。{dish}第{step + 1}步建议计时{_kitchen_format_duration(default_sec)}。"

    if any(x in text for x in ("怎么做", "怎么烧", "怎么煮", "怎么炒", "怎么烤", "这一步", "现在做什么", "接下来做什么")):
        return f"逐光暂时无法连接 OpenClaw，不过当前菜谱仍可使用。{dish}第{step + 1}步：{step_text[:180]}"

    if any(x in text for x in ("注意", "关键", "提醒", "要点")):
        points = [str(x).strip() for x in (recipe.get("key_points") or []) if str(x).strip()]
        if points:
            return "逐光暂时无法连接 OpenClaw。当前菜谱关键点：" + "；".join(points[:3])
    return None


async def _apply_kitchen_voice_command(session: ClientSession | None, transcript: str) -> str | None:
    text = transcript.strip()
    compact = re.sub(r"[\s，。！？、,.!?]", "", text)
    _, kitchen_namespace = _kitchen_voice_namespace(text)
    terminal_source = _kitchen_voice_source_is_terminal(session)
    kitchen_named = kitchen_namespace or ("厨房终端" in text) or ("厨房屏" in text)
    fast_intent = _kitchen_fast_control_intent(text)
    if fast_intent and not (kitchen_named or terminal_source):
        # A3.0b keeps 厨房 as the domain namespace on other HomeAI devices.
        # Future iPad PTT is already a KitchenTerminal source, so it may omit the prefix.
        fast_intent = None
    if fast_intent:
        print(f"[KITCHEN-INTENT] intent={fast_intent} source=local-fastpath namespace={'kitchen-terminal' if terminal_source else '厨房'} transcript={text!r}")

    show_today = (fast_intent == "menu.today") or (
        (kitchen_named or terminal_source)
        and (("今天" in text or "今日" in text) and any(word in text for word in ("菜谱", "菜单", "晚餐", "晚饭")))
        and (
            any(word in text for word in ("显示", "打开", "看看", "看一下", "拉取", "加载", "调出", "调出来", "放到厨房", "投到厨房"))
            or compact in {"今天菜单", "今日菜单", "今天晚餐", "今日晚餐"}
        )
    )
    if show_today:
        try:
            delivered, menu = await _kitchen_show_today_menu()
        except KitchenMenuError as exc:
            print(f"[KITCHEN-VOICE] today menu failed: {exc}")
            return "我没有找到今天可以显示的菜单。"
        count = len(menu.get("items") or [])
        if delivered:
            return f"已经在厨房终端打开今天的菜单，共{count}道。"
        return f"今天的菜单已经准备好了，共{count}道，不过厨房终端现在没有连接。"

    active = KITCHEN_CURRENT_STATE.get("screen") != "idle" and KITCHEN_CURRENT_MENU is not None

    if fast_intent == "menu.shopping":
        try:
            delivered = await _kitchen_show_shopping()
        except KitchenMenuError:
            return "我没有找到今天的购物清单。"
        return "购物清单已经显示在厨房终端。" if delivered else "购物清单已经准备好，但厨房终端现在没有连接。"

    if fast_intent == "menu.timeline":
        try:
            delivered = await _kitchen_show_timeline()
        except KitchenMenuError:
            return "我没有找到今天的烧菜顺序。"
        return "今天的烧菜顺序已经显示在厨房终端。" if delivered else "烧菜顺序已经准备好，但厨房终端现在没有连接。"

    if fast_intent == "step.next" and active:
        delivered, recipe, step = await _kitchen_move_step(1)
        if recipe is None:
            return "厨房终端现在没有打开具体菜谱。"
        steps = recipe.get("steps") or []
        if not steps:
            return "这道菜没有可用步骤。"
        prefix = "已经是最后一步。" if step >= len(steps) - 1 else "下一步。"
        return prefix + str(steps[step])[:120]

    if fast_intent == "step.prev" and active:
        delivered, recipe, step = await _kitchen_move_step(-1)
        if recipe is None:
            return "厨房终端现在没有打开具体菜谱。"
        steps = recipe.get("steps") or []
        return "上一步。" + (str(steps[step])[:120] if steps else "")

    if fast_intent == "menu.back" and active:
        await _kitchen_show_menu(KITCHEN_CURRENT_MENU)
        return "已经返回今天的菜单。"

    finish_phrases = ("结束今天的烹饪", "结束今日烹饪", "今天做完了", "今天烧完了", "今天就到这里", "结束烹饪")
    if fast_intent == "day.finish" or any(phrase in text for phrase in finish_phrases):
        try:
            await _kitchen_begin_finish(speak=False)
        except KitchenMenuError:
            return "今天还没有加载厨房菜单。"
        return "已经打开今日烹饪的结束确认。确认后，我会问你有没有菜谱要保存到私房菜。"

    screen = str(KITCHEN_CURRENT_STATE.get("screen") or "")
    if screen == "finish" and compact in {"逐光确认结束", "确认结束", "确认", "结束吧", "是的结束", "结束"}:
        await _kitchen_confirm_finish(speak=False)
        return "今天的计时器已经关闭。有没有想保存到私房菜的菜谱？你可以说保存河虾，全部保存，或者都不保存。"

    if screen == "save_private":
        if any(phrase in text for phrase in ("都不保存", "不保存", "没有要保存", "没有", "直接结束")):
            await _kitchen_finalize_day([], speak=False)
            return "好的，今天没有保存新的私房菜。今日烹饪已经结束，辛苦了。"
        menu = KITCHEN_CURRENT_MENU
        if menu is not None and any(word in text for word in ("保存", "私房菜")):
            names: list[str] = []
            if any(phrase in text for phrase in ("全部保存", "都保存", "全都保存")):
                names = [str(x.get("name") or "") for x in menu.get("items") or [] if str(x.get("name") or "")]
            else:
                for item in menu.get("items") or []:
                    name = str(item.get("name") or "")
                    parts = [p for p in re.split(r"[·（）()\s]+", name) if p]
                    if name and (name in text or any(len(part) >= 2 and part in text for part in parts)):
                        names.append(name)
                if not names:
                    query = re.sub(r"逐光|保存|到|进|私房菜|菜谱|这道菜", "", text).strip(" ，。！？、")
                    matched = match_recipe(menu, query) if query else None
                    if matched is not None:
                        names = [str(matched.get("name") or "")]
            if names:
                _, saved = await _kitchen_finalize_day(names, speak=False)
                if saved:
                    return "已经把" + "、".join(saved) + "保存到私房菜。今天的烹饪也结束了，辛苦了。"
            return "我没有确定你想保存哪道菜。可以直接说，比如，保存河虾。"

    # Kitchen timers are local Gateway state, so common voice controls do not
    # need an OpenClaw round-trip. Current recipe/step is the default target.
    timer_context = kitchen_named or active or ("计时" in text) or ("定时" in text)
    if timer_context and any(phrase in text for phrase in ("还有多久", "还剩多久", "剩多久", "剩多长时间")):
        timer = _kitchen_pick_timer(text)
        if timer is None:
            return "现在没有正在运行的厨房计时器。"
        if timer.status == "finished":
            return f"{timer.dish}第{timer.step + 1}步的计时已经结束。"
        remaining = _kitchen_timer_remaining(timer)
        prefix = "暂停中，" if timer.status == "paused" else ""
        return f"{timer.dish}第{timer.step + 1}步{prefix}还剩{_kitchen_format_duration(remaining)}。"

    if timer_context and any(phrase in text for phrase in ("暂停计时", "暂停定时", "计时暂停")):
        timer = _kitchen_pick_timer(text)
        if timer is None:
            return "现在没有可以暂停的厨房计时器。"
        _kitchen_timer_pause(timer)
        return f"已经暂停{timer.dish}的计时，还剩{_kitchen_format_duration(_kitchen_timer_remaining(timer))}。"

    if timer_context and any(phrase in text for phrase in ("继续计时", "恢复计时", "继续定时", "恢复定时")):
        timer = _kitchen_pick_timer(text)
        if timer is None:
            return "现在没有可以继续的厨房计时器。"
        _kitchen_timer_resume(timer)
        return f"已经继续{timer.dish}的计时，还剩{_kitchen_format_duration(_kitchen_timer_remaining(timer))}。"

    if timer_context and any(phrase in text for phrase in ("取消计时", "停止计时", "取消定时", "停止定时")):
        timer = _kitchen_pick_timer(text)
        if timer is None:
            return "现在没有可以取消的厨房计时器。"
        dish = timer.dish
        _kitchen_timer_remove(timer)
        return f"已经取消{dish}的计时。"

    duration = _kitchen_duration_from_voice(text)
    if timer_context and duration is not None and any(word in text for word in ("再加", "加上", "增加", "延长")):
        timer = _kitchen_pick_timer(text)
        if timer is None:
            return "现在没有可以延长的厨房计时器。"
        _kitchen_timer_adjust(timer, duration)
        return f"已经增加{_kitchen_format_duration(duration)}，现在还剩{_kitchen_format_duration(_kitchen_timer_remaining(timer))}。"

    if timer_context and duration is not None and any(word in text for word in ("减少", "减掉", "减去")):
        timer = _kitchen_pick_timer(text)
        if timer is None:
            return "现在没有可以调整的厨房计时器。"
        _kitchen_timer_adjust(timer, -duration)
        return f"已经减少{_kitchen_format_duration(duration)}，现在还剩{_kitchen_format_duration(_kitchen_timer_remaining(timer))}。"

    start_timer = timer_context and (
        any(phrase in text for phrase in ("开始计时", "开始定时", "启动计时", "启动定时"))
        or (("计时" in text or "定时" in text) and duration is not None)
    )
    if start_timer:
        try:
            timer = _kitchen_timer_start(duration)
        except KitchenMenuError:
            return "当前步骤没有默认计时。你可以直接说，比如，计时三十秒。"
        return f"好的，{timer.dish}第{timer.step + 1}步开始计时{_kitchen_format_duration(timer.duration_sec)}。"

    if (kitchen_named or active) and "购物清单" in text and any(word in text for word in ("显示", "打开", "看看", "看", "给我")):
        try:
            delivered = await _kitchen_show_shopping()
        except KitchenMenuError:
            return "我没有找到今天的购物清单。"
        return "购物清单已经显示在厨房终端。" if delivered else "购物清单已经准备好，但厨房终端现在没有连接。"

    if (kitchen_named or active) and any(word in text for word in ("烧菜顺序", "烹饪顺序", "做菜顺序", "操作时间线")):
        try:
            delivered = await _kitchen_show_timeline()
        except KitchenMenuError:
            return "我没有找到今天的烧菜顺序。"
        return "今天的烧菜顺序已经显示在厨房终端。" if delivered else "烧菜顺序已经准备好，但厨房终端现在没有连接。"

    if active and (compact in {"逐光下一步", "下一步", "下一页", "继续", "接下来", "然后呢", "往下", "继续下一步"} or any(p in text for p in ("下一步怎么做", "接下来怎么做", "然后怎么做"))):
        delivered, recipe, step = await _kitchen_move_step(1)
        if recipe is None:
            return "厨房终端现在没有打开具体菜谱。"
        steps = recipe.get("steps") or []
        if not steps:
            return "这道菜没有可用步骤。"
        text_step = str(steps[step])
        if step >= len(steps) - 1:
            prefix = "已经是最后一步。"
        else:
            prefix = "下一步。"
        return prefix + text_step[:120]

    if active and (compact in {"逐光上一步", "上一步", "上一页", "返回上一步", "往回一步", "退一步"} or "刚才那一步" in text):
        delivered, recipe, step = await _kitchen_move_step(-1)
        if recipe is None:
            return "厨房终端现在没有打开具体菜谱。"
        steps = recipe.get("steps") or []
        return "上一步。" + (str(steps[step])[:120] if steps else "")

    if active and (compact in {"逐光返回菜单", "返回菜单", "回到菜单", "回菜单", "今日菜单", "回到今日菜单", "看菜单"} or "回到今天的菜单" in text):
        await _kitchen_show_menu(KITCHEN_CURRENT_MENU)
        return "已经返回今天的菜单。"

    if kitchen_named or (active and any(word in text for word in ("打开", "显示", "看看", "看一下", "继续做", "接着做", "切到", "回到", "做到哪"))):
        try:
            menu = KITCHEN_CURRENT_MENU
            if menu is None or str(menu.get("date") or "") != _kitchen_today():
                menu = _kitchen_load()
            query = _kitchen_voice_query_after_open(text)
            recipe = match_recipe(menu, query)
            if recipe is None:
                # Fallback: match any unique dish name fragment appearing in the utterance.
                candidates = []
                for item in menu.get("items") or []:
                    name = str(item.get("name") or "")
                    if name and (name in text or any(part and part in text for part in re.split(r"[·（）()\s]+", name))):
                        r = recipe_for(menu, name)
                        if r is not None and r not in candidates:
                            candidates.append(r)
                if len(candidates) == 1:
                    recipe = candidates[0]
            if recipe is not None:
                delivered = await _kitchen_open_recipe(recipe, None)
                steps = recipe.get("steps") or []
                current_step = int(KITCHEN_CURRENT_STATE.get("step") or 0)
                current_text = str(steps[current_step])[:100] if steps else ""
                prefix = f"已经继续打开{recipe.get('name')}第{current_step + 1}步。"
                if delivered:
                    return prefix + current_text
                return f"{recipe.get('name')}第{current_step + 1}步已经准备好，但厨房终端现在没有连接。"
        except KitchenMenuError:
            pass

    return None


def _kitchen_qa_public() -> dict[str, Any]:
    return {
        "status": str(KITCHEN_QA_STATE.get("status") or "idle"),
        "request_id": str(KITCHEN_QA_STATE.get("request_id") or ""),
        "transcript": str(KITCHEN_QA_STATE.get("transcript") or ""),
        "answer": str(KITCHEN_QA_STATE.get("answer") or ""),
        "error": str(KITCHEN_QA_STATE.get("error") or ""),
        "audio_event_id": str(KITCHEN_QA_STATE.get("audio_event_id") or ""),
        "started_at": float(KITCHEN_QA_STATE.get("started_at") or 0.0),
        "updated_at": float(KITCHEN_QA_STATE.get("updated_at") or 0.0),
    }


def _kitchen_qa_set(
    status: str,
    *,
    request_id: str | None = None,
    transcript: str | None = None,
    answer: str | None = None,
    error: str | None = None,
    audio_event_id: str | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    now = time.time()
    if reset:
        KITCHEN_QA_STATE.update({
            "status": "idle", "request_id": "", "transcript": "", "answer": "",
            "error": "", "audio_event_id": "", "started_at": 0.0, "updated_at": now,
        })
        return _kitchen_qa_public()
    if request_id is not None:
        KITCHEN_QA_STATE["request_id"] = request_id
    KITCHEN_QA_STATE["status"] = str(status or "idle")
    if transcript is not None:
        KITCHEN_QA_STATE["transcript"] = transcript
    if answer is not None:
        KITCHEN_QA_STATE["answer"] = answer
    if error is not None:
        KITCHEN_QA_STATE["error"] = error
    if audio_event_id is not None:
        KITCHEN_QA_STATE["audio_event_id"] = audio_event_id
    if status in {"listening", "uploading"}:
        KITCHEN_QA_STATE["started_at"] = now
        KITCHEN_QA_STATE["transcript"] = ""
        KITCHEN_QA_STATE["answer"] = ""
        KITCHEN_QA_STATE["error"] = ""
        KITCHEN_QA_STATE["audio_event_id"] = ""
    KITCHEN_QA_STATE["updated_at"] = now
    return _kitchen_qa_public()


async def _kitchen_qa_notify(session: KitchenSession, msg: dict[str, Any]) -> None:
    try:
        await send_json(session.ws, msg)
    except Exception:
        # Completion is recovered from /kitchen/view if the upload socket drops.
        pass


def build_kitchen_agent_prompt(transcript: str) -> str:
    context = _kitchen_agent_context_text()
    return f"""你是家庭AI助手“逐光”，现在通过 KitchenTerminal 与用户在厨房里语音交流。
回答要自然、简洁、适合直接语音播报。默认 1～4 句，通常控制在 180 个中文字符以内；只有用户明确要求详细步骤时才展开。
不要使用 Markdown 表格，不要输出标题符号，不要复述系统说明。

你会收到 KitchenTerminal 的实时上下文，包括今天菜单、当前菜品、当前步骤、完整菜谱关键点和正在运行的计时器。用户说“这个”“这一步”“现在这个”时，优先指当前菜品与当前步骤。
如果问题是“为什么这么做”“没有某个调料怎么办”“太咸/太淡怎么补救”“这个步骤要多久”等，直接结合当前菜谱回答。
如果用户询问食物是否已经熟、颜色/状态是否正常，但你没有视觉输入，不要假装看见食物；请给出用户可以现场判断的具体标准，需要时再问一个最关键的确认问题。
不要声称已经操作 KitchenTerminal。页面切换、计时器、结束烹饪等确定性控制由 Gateway 本地指令完成。

{context}

用户现在问：{transcript}
"""


async def openclaw_kitchen_chat(transcript: str) -> str:
    headers = {"Content-Type": "application/json"}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"
    kitchen_user = os.getenv("HOMEAI_KITCHEN_OPENCLAW_USER", "home-ai-kitchen:main").strip() or "home-ai-kitchen:main"
    payload = {
        "model": OPENCLAW_MODEL,
        "user": kitchen_user,
        "stream": False,
        "messages": [{"role": "user", "content": build_kitchen_agent_prompt(transcript)}],
    }
    async with _openclaw_http_client(OPENCLAW_KITCHEN_TIMEOUT_SEC) as client:
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
    answer = str(content).strip()
    if not answer:
        raise RuntimeError("OpenClaw returned empty KitchenTerminal answer")
    return answer


async def _process_kitchen_qa(session: KitchenSession) -> None:
    if session.qa_processing:
        return
    session.qa_processing = True
    request_id = session.qa_request_id or ("kq-" + uuid.uuid4().hex[:12])
    wav_bytes = bytes(session.qa_audio)
    started = time.perf_counter()
    try:
        if len(wav_bytes) < 800:
            raise RuntimeError("录音太短，请再说一次")
        if len(wav_bytes) > KITCHEN_QA_MAX_BYTES:
            raise RuntimeError("录音过长，请控制在二十五秒左右")
        if not (wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE"):
            raise RuntimeError("录音格式不是 WAV")

        _best_effort_write_bytes(HOMEAI_DEBUG_DIR / "latest_input_kitchen.wav", wav_bytes)
        state = _kitchen_qa_set("recognizing", request_id=request_id)
        await _kitchen_qa_notify(session, {"type": "kitchen.qa.state", "qa": state})
        print(f"[KITCHEN-QA] recognizing id={request_id} bytes={len(wav_bytes)}")

        asr_started = time.perf_counter()
        transcript, asr_used = await transcribe_audio(wav_bytes)
        asr_ms = int((time.perf_counter() - asr_started) * 1000)
        transcript = str(transcript or "").strip()
        if not transcript:
            raise RuntimeError("没有识别到语音，请再说一次")
        print(f"[KITCHEN-QA][ASR:{asr_used}] {transcript}")
        state = _kitchen_qa_set("thinking", request_id=request_id, transcript=transcript)
        await _kitchen_qa_notify(session, {"type": "kitchen.qa.transcript", "text": transcript, "qa": state})

        agent_started = time.perf_counter()
        answer = await _apply_kitchen_voice_command(session, transcript)
        route = "local-control" if answer is not None else "openclaw"
        if answer is None:
            print(f"[KITCHEN-QA] OpenClaw begin id={request_id} model={OPENCLAW_MODEL}")
            try:
                answer = await openclaw_kitchen_chat(transcript)
            except Exception as exc:
                fallback = _kitchen_offline_fallback(transcript)
                if fallback is None:
                    raise
                answer = fallback
                route = "offline-fallback"
                print(f"[KITCHEN-QA] OpenClaw unavailable; fallback id={request_id} error={type(exc).__name__}: {exc}")
        agent_ms = int((time.perf_counter() - agent_started) * 1000)
        answer = str(answer or "").strip()
        if not answer:
            raise RuntimeError("逐光没有生成回答")
        print(f"[KITCHEN-QA] answer route={route} id={request_id} chars={len(answer)}")
        _vlog(f"[KITCHEN-QA-TEXT] id={request_id} text={answer!r}")

        state = _kitchen_qa_set("speaking", request_id=request_id, transcript=transcript, answer=answer)
        await _kitchen_qa_notify(session, {"type": "kitchen.qa.answer", "text": answer, "qa": state})

        tts_started = time.perf_counter()
        audio = await _kitchen_speak(answer, kind="assistant", source_id=request_id)
        tts_ms = int((time.perf_counter() - tts_started) * 1000)
        audio_event_id = str((audio or {}).get("event_id") or "")
        state = _kitchen_qa_set(
            "done", request_id=request_id, transcript=transcript, answer=answer,
            error="", audio_event_id=audio_event_id,
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        print(f"[KITCHEN-QA] done id={request_id} asr={asr_ms}ms agent={agent_ms}ms tts={tts_ms}ms total={total_ms}ms audio={audio_event_id or 'none'}")
        await _kitchen_qa_notify(session, {
            "type": "kitchen.qa.result", "ok": True, "transcript": transcript,
            "answer": answer, "audio": audio, "qa": state,
        })
    except Exception as exc:
        message = str(exc)[:240] or type(exc).__name__
        state = _kitchen_qa_set("error", request_id=request_id, error=message)
        print(f"[KITCHEN-QA-ERROR] id={request_id} {type(exc).__name__}: {exc}")
        _log_traceback()
        await _kitchen_qa_notify(session, {"type": "kitchen.qa.error", "message": message, "qa": state})
    finally:
        session.qa_processing = False
        session.qa_receiving = False
        session.qa_audio.clear()


async def handle_kitchen_connection(ws: Any) -> None:
    session = KitchenSession(ws=ws)
    KITCHEN_SESSIONS.append(session)
    print("[KITCHEN] terminal connected")
    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                if session.qa_receiving and not session.qa_processing:
                    if len(session.qa_audio) + len(raw) > KITCHEN_QA_MAX_BYTES:
                        session.qa_receiving = False
                        session.qa_audio.clear()
                        state = _kitchen_qa_set("error", request_id=session.qa_request_id, error="录音过长，请控制在二十五秒左右")
                        await _kitchen_qa_notify(session, {"type": "kitchen.qa.error", "message": state["error"], "qa": state})
                    else:
                        session.qa_audio.extend(raw)
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = str(msg.get("type") or "")
            if kind == "kitchen.hello":
                device_id = str(msg.get("device_id") or KITCHEN_DEVICE_ID).strip()
                session.device_id = device_id or KITCHEN_DEVICE_ID
                session.hello_received = True
                await send_json(ws, {
                    "type": "kitchen.ready",
                    "protocol": KITCHEN_PROTOCOL,
                    "device_id": session.device_id,
                })
                await send_json(ws, {
                    "type": "kitchen.sync",
                    "protocol": KITCHEN_PROTOCOL,
                    "view": KITCHEN_CURRENT_VIEW,
                })
                print(f"[KITCHEN] hello id={session.device_id}")
            elif kind == "kitchen.qa.start":
                if session.qa_processing:
                    await _kitchen_qa_notify(session, {"type": "kitchen.qa.error", "message": "上一条问题还在处理中", "qa": _kitchen_qa_public()})
                    continue
                session.qa_request_id = str(msg.get("request_id") or ("kq-" + uuid.uuid4().hex[:12]))[:80]
                session.qa_audio.clear()
                session.qa_receiving = True
                state = _kitchen_qa_set("uploading", request_id=session.qa_request_id)
                await _kitchen_qa_notify(session, {"type": "kitchen.qa.state", "qa": state})
                print(f"[KITCHEN-QA] upload start id={session.qa_request_id} device={session.device_id}")
            elif kind == "kitchen.qa.stop":
                if not session.qa_receiving:
                    await _kitchen_qa_notify(session, {"type": "kitchen.qa.error", "message": "没有收到录音", "qa": _kitchen_qa_public()})
                    continue
                session.qa_receiving = False
                print(f"[KITCHEN-QA] upload complete id={session.qa_request_id} bytes={len(session.qa_audio)}")
                await _process_kitchen_qa(session)
            elif kind == "kitchen.qa.cancel":
                session.qa_receiving = False
                session.qa_audio.clear()
                _kitchen_qa_set("idle", reset=True)
            elif kind == "kitchen.item.select":
                session.current_item = str(msg.get("item") or "")
                print(f"[KITCHEN] item.select id={session.device_id} index={msg.get('index')} item={session.current_item!r}")
                try:
                    delivered, recipe = await _kitchen_open_recipe_by_name(session.current_item, 0)
                    if recipe is None:
                        print(f"[KITCHEN-WARN] no recipe for selected item={session.current_item!r}")
                except KitchenMenuError as exc:
                    print(f"[KITCHEN-WARN] selected item open failed: {exc}")
            elif kind == "kitchen.nav":
                action = str(msg.get("action") or "").strip().lower()
                try:
                    if action == "next":
                        await _kitchen_move_step(1)
                    elif action == "prev":
                        await _kitchen_move_step(-1)
                    elif action == "menu":
                        menu = KITCHEN_CURRENT_MENU or _kitchen_load()
                        await _kitchen_show_menu(menu)
                    elif action == "shopping":
                        await _kitchen_show_shopping()
                    elif action == "timeline":
                        await _kitchen_show_timeline()
                except KitchenMenuError as exc:
                    print(f"[KITCHEN-WARN] nav action={action!r} failed: {exc}")
            elif kind == "kitchen.state":
                session.current_view = str(msg.get("view") or session.current_view)
                session.current_item = str(msg.get("item") or session.current_item)
                try:
                    session.current_step = max(0, int(msg.get("step") or 0))
                except (TypeError, ValueError):
                    session.current_step = 0
    except ConnectionClosed as exc:
        print(f"[KITCHEN] terminal closed code={getattr(exc, 'code', None)} reason={getattr(exc, 'reason', '')!r}")
    finally:
        # If the dedicated Q&A upload socket disappears after qa.start but before
        # qa.stop, the old code left the global state stuck at `uploading`. The
        # iPad then kept rendering a busy Q&A button which looked completely dead.
        if session.qa_receiving and not session.qa_processing:
            abandoned_id = session.qa_request_id
            session.qa_receiving = False
            session.qa_audio.clear()
            _kitchen_qa_set("error", request_id=abandoned_id, error="录音连接中断，请重新点击问逐光")
            print(f"[KITCHEN-QA-WARN] abandoned upload reset id={abandoned_id or 'unknown'}")
        if session in KITCHEN_SESSIONS:
            KITCHEN_SESSIONS.remove(session)
        print(f"[KITCHEN] terminal disconnected id={session.device_id}")


def _is_loopback_peer(ws: Any) -> bool:
    peer = getattr(ws, "remote_address", None)
    if isinstance(peer, tuple) and peer:
        host = str(peer[0] or "")
        return host in {"127.0.0.1", "::1"} or host.startswith("::ffff:127.")
    return False


async def handle_kitchen_control(ws: Any) -> None:
    # Local-only A1 debug/control socket used by kitchen_send.py.
    if not _is_loopback_peer(ws):
        await ws.close(code=1008, reason="kitchen control is localhost-only")
        return
    print("[KITCHEN] local control connected")
    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = str(msg.get("type") or "")
            if kind in {"kitchen.show_idle", "kitchen.show_menu", "kitchen.show_message"}:
                delivered = await kitchen_broadcast(msg)
                await send_json(ws, {
                    "type": "kitchen.control.ack",
                    "status": "applied",
                    "command": kind,
                    "clients": delivered,
                    "revision": KITCHEN_CURRENT_VIEW.get("revision"),
                })
            elif kind == "kitchen.control.today":
                try:
                    delivered, menu = await _kitchen_show_today_menu()
                    await send_json(ws, {
                        "type": "kitchen.control.ack",
                        "status": "applied",
                        "command": kind,
                        "clients": delivered,
                        "date": menu.get("date"),
                        "dishes": len(menu.get("items") or []),
                    })
                except KitchenMenuError as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind == "kitchen.control.recipe":
                try:
                    raw_step = msg.get("step")
                    delivered, recipe = await _kitchen_open_recipe_by_name(str(msg.get("dish") or ""), int(raw_step) if raw_step is not None else None)
                    if recipe is None:
                        raise KitchenMenuError("dish not found")
                    await send_json(ws, {
                        "type": "kitchen.control.ack",
                        "status": "applied",
                        "command": kind,
                        "clients": delivered,
                        "dish": recipe.get("name"),
                        "step": KITCHEN_CURRENT_STATE.get("step"),
                    })
                except (KitchenMenuError, ValueError, TypeError) as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind == "kitchen.control.shopping":
                try:
                    delivered = await _kitchen_show_shopping()
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "clients": delivered})
                except KitchenMenuError as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind == "kitchen.control.timeline":
                try:
                    delivered = await _kitchen_show_timeline()
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "clients": delivered})
                except KitchenMenuError as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind == "kitchen.control.finish":
                try:
                    delivered = await _kitchen_begin_finish(speak=False)
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "clients": delivered})
                except KitchenMenuError as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind == "kitchen.control.finish_confirm":
                delivered = await _kitchen_confirm_finish(speak=False)
                await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "clients": delivered})
            elif kind == "kitchen.control.finish_save":
                names = msg.get("dishes") if isinstance(msg.get("dishes"), list) else []
                delivered, saved = await _kitchen_finalize_day([str(x) for x in names], speak=False)
                await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "clients": delivered, "saved": saved})
            elif kind == "kitchen.control.timer_start":
                try:
                    raw_seconds = msg.get("seconds")
                    timer = _kitchen_timer_start(int(raw_seconds) if raw_seconds is not None else None)
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "timer": _kitchen_timer_public(timer)})
                except (KitchenMenuError, ValueError, TypeError) as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind in {"kitchen.control.timer_adjust", "kitchen.control.timer_set", "kitchen.control.timer_pause", "kitchen.control.timer_resume", "kitchen.control.timer_cancel"}:
                timer = KITCHEN_TIMERS.get(str(msg.get("timer_id") or "")) or _kitchen_pick_timer()
                if timer is None:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": "timer not found"})
                    continue
                try:
                    if kind == "kitchen.control.timer_adjust":
                        _kitchen_timer_adjust(timer, int(msg.get("seconds") or 0))
                    elif kind == "kitchen.control.timer_set":
                        _kitchen_timer_set(timer, int(msg.get("seconds") or 0))
                    elif kind == "kitchen.control.timer_pause":
                        _kitchen_timer_pause(timer)
                    elif kind == "kitchen.control.timer_resume":
                        _kitchen_timer_resume(timer)
                    else:
                        _kitchen_timer_remove(timer)
                        timer = None
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "applied", "command": kind, "timer": (_kitchen_timer_public(timer) if timer is not None else None), "timers": _kitchen_timer_snapshot()})
                except (KitchenMenuError, ValueError, TypeError) as exc:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": str(exc)})
            elif kind == "kitchen.control.speak":
                text = str(msg.get("text") or "").strip()
                if not text:
                    await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": "text is empty"})
                    continue
                event = await _kitchen_speak(text, kind=str(msg.get("kind") or "debug"))
                await send_json(ws, {"type": "kitchen.control.ack", "status": "applied" if event else "rejected", "command": kind, "audio": event})
            elif kind == "kitchen.control.status":
                await send_json(ws, {
                    "type": "kitchen.control.status",
                    "protocol": KITCHEN_PROTOCOL,
                    "clients": len(KITCHEN_SESSIONS),
                    "menu_dir": str(KITCHEN_MENU_DIR),
                    "state": KITCHEN_CURRENT_STATE,
                    "view": KITCHEN_CURRENT_VIEW,
                    "timers": _kitchen_timer_snapshot(),
                    "audio": _kitchen_audio_public(),
                })
            else:
                await send_json(ws, {"type": "kitchen.control.ack", "status": "rejected", "message": "unsupported control command"})
    except ConnectionClosed:
        pass
    finally:
        print("[KITCHEN] local control disconnected")


async def handle_connection(ws) -> None:
    request = getattr(ws, "request", None)
    path = getattr(request, "path", "") if request is not None else ""
    clean_path = path.split("?", 1)[0] if path else WS_PATH

    if clean_path == KITCHEN_WS_PATH:
        await handle_kitchen_connection(ws)
        return
    if clean_path == KITCHEN_CONTROL_PATH:
        await handle_kitchen_control(ws)
        return
    if clean_path != WS_PATH:
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
                _apply_device_hello(session, msg)

                if _is_speaker_session(session):
                    print(
                        f"[DEVICE] hello id={session.device_id} role=speaker "
                        f"parent={session.parent_device_id or '-'} "
                        f"priority={session.audio_priority}"
                    )
                    await send_json(ws, _device_ready_payload(session))
                    if not session.parent_device_id:
                        await send_json(ws, {
                            "type": "speaker.error",
                            "message": "parent_device_id required",
                        })
                    continue

                print(
                    f"[DEVICE] hello id={session.device_id} role=companion "
                    f"openclaw_user={session.openclaw_user}"
                )
                await send_json(ws, _device_ready_payload(session))
                await send_state(ws, "idle")

                if _supports_display_policy(session):
                    # Do not await here: this coroutine must keep receiving so
                    # the device's display.ack can be processed.
                    start_display_policy_task(
                        session,
                        reason="device_hello",
                    )
                else:
                    _vlog(
                        f"[DISPLAY] policy unsupported; skip id={session.device_id}"
                    )

                if _supports_info_feed(session):
                    await send_info_sync(session)
                else:
                    _vlog(
                        f"[INFO] feed unsupported; skip id={session.device_id}"
                    )

            elif kind == "speaker.volume.ack":
                if not _is_speaker_session(session):
                    continue
                try:
                    level = max(0, min(100, int(msg.get("volume_percent"))))
                    session.capabilities["volume_percent"] = level
                except (TypeError, ValueError):
                    level = -1
                print(
                    f"[VOLUME] ACK speaker={session.device_id} "
                    f"status={msg.get('status')} volume={level}% "
                    f"request={msg.get('request_id')}"
                )
                session.speaker_volume_ack_queue.put_nowait(dict(msg))

            elif kind == "display.ack":
                command_id = str(msg.get("command_id") or "")
                status = str(msg.get("status") or "")
                requested = str(msg.get("requested") or "")
                sleeping = bool(msg.get("sleeping"))
                busy = bool(msg.get("busy"))
                manual_wake = bool(msg.get("manual_wake_active"))

                _vlog(
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
                elif (
                    command_id
                    and command_id
                    == str(session.display_last_ack.get("command_id") or "")
                ):
                    # Some existing firmware paths can emit the same final
                    # applied ACK twice. It is already confirmed, so this is
                    # benign and should not pollute logs as a stale warning.
                    _vlog(
                        f"[DISPLAY] duplicate ACK ignored "
                        f"command_id={command_id}"
                    )
                else:
                    print(
                        f"[DISPLAY-WARN] unexpected/stale device ACK "
                        f"expected={session.display_expected_command_id!r} "
                        f"got={command_id!r}"
                    )

            elif kind == "glass2.ack":
                command_id = str(msg.get("command_id") or "")
                requested = str(msg.get("requested") or "")
                status = str(msg.get("status") or "")
                visible = bool(msg.get("visible"))
                _vlog(
                    f"[GLASS2] device ACK requested={requested} "
                    f"status={status} visible={visible} command_id={command_id}"
                )
                if (
                    session.glass2_expected_command_id
                    and command_id == session.glass2_expected_command_id
                ):
                    session.glass2_ack_queue.put_nowait(dict(msg))
                else:
                    print(
                        f"[GLASS2-WARN] unexpected/stale ACK "
                        f"expected={session.glass2_expected_command_id!r} "
                        f"got={command_id!r}"
                    )

            elif kind == "info.ack":
                _vlog(f"[INFO] device ack revision={FEED_REVISION}")

            elif kind == "ptt.start":
                if _is_speaker_session(session):
                    await send_json(ws, {
                        "type": "gateway.error",
                        "message": "speaker role cannot start PTT",
                    })
                    continue
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
                _vlog("[AUDIO] device TTS slot ready")
                if session.playback_sequence_active:
                    session.playback_completed_segments = min(
                        session.playback_total_segments,
                        session.playback_completed_segments + 1,
                    )
                    session.playback_slot_ready_event.set()

            elif kind == "playback.done":
                print("[AUDIO] device playback done")
                if session.playback_sequence_active:
                    session.playback_completed_segments = session.playback_total_segments
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
        if session.playback_sequence_active:
            # Wake any send_pcm_for_playback waiter immediately. Without this,
            # a dead speaker socket can sit until the playback timeout expires.
            session.playback_error_event.set()
        if session.display_policy_task is not None:
            session.display_policy_task.cancel()
        if session in ACTIVE_SESSIONS:
            ACTIVE_SESSIONS.remove(session)
        print(
            f"[WS] client disconnected id={session.device_id} "
            f"role={session.device_role}"
        )


async def preflight(*, require_openclaw: bool = True) -> int:
    print("=== HomeAIAgent Gateway persistent-config preflight ===")
    if (
        VOLCENGINE_TTS_RESOURCE_ID != STANDARD_TTS_RESOURCE_ID
        or VOLCENGINE_TTS_VOICE != STANDARD_TTS_VOICE
    ):
        print(
            "[FAIL] standard TTS guard rejected active pairing: "
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
        f"[CFG] OpenClaw http trust_env={_openclaw_http_trust_env()} "
        f"timeouts=chat:{OPENCLAW_CHAT_TIMEOUT_SEC:.0f}s/"
        f"kitchen:{OPENCLAW_KITCHEN_TIMEOUT_SEC:.0f}s/"
        f"info:{OPENCLAW_INFO_TIMEOUT_SEC:.0f}s"
    )
    print(
        f"[CFG] OpenClaw transport={OPENCLAW_TRANSPORT} "
        f"ssh={OPENCLAW_SSH_USER}@{OPENCLAW_SSH_HOST} "
        f"local=127.0.0.1:{OPENCLAW_LOCAL_PORT} remote=127.0.0.1:{OPENCLAW_REMOTE_PORT}"
    )
    print(f"[CFG] primary session user={OPENCLAW_USER}")
    print(
        f"[CFG] device router primary={HOMEAI_PRIMARY_DEVICE_ID}->{OPENCLAW_USER} "
        f"mini={HOMEAI_MINI_DEVICE_ID}->{OPENCLAW_MINI_USER} "
        f"auto_isolate_unknown={HOMEAI_AUTO_ISOLATE_UNKNOWN_COMPANIONS}"
    )
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

    if OPENCLAW_TRANSPORT == "embedded_ssh" and (not OPENCLAW_SSH_USER or not OPENCLAW_SSH_HOST):
        print("[FAIL] embedded SSH requires OPENCLAW_SSH_USER and OPENCLAW_SSH_HOST in persistent config.")
        return 2

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
        async with _openclaw_http_client(10) as client:
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
        VOLCENGINE_TTS_RESOURCE_ID != STANDARD_TTS_RESOURCE_ID
        or VOLCENGINE_TTS_VOICE != STANDARD_TTS_VOICE
    ):
        raise RuntimeError(
            "standard TTS guard rejected active pairing: "
            f"resource={VOLCENGINE_TTS_RESOURCE_ID} voice={VOLCENGINE_TTS_VOICE}"
        )

    REMINDER_LOCK = asyncio.Lock()
    load_reminder_queue()
    load_kitchen_timers()
    load_kitchen_progress()
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

    # Restart is cache-only for Info. The cache was already loaded by
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
    print(f"[KITCHEN] UI http://<gateway-lan-ip>:{PORT}{KITCHEN_HTTP_PATH}")
    print(f"[KITCHEN] WS ws://<gateway-lan-ip>:{PORT}{KITCHEN_WS_PATH}")
    async with websockets.serve(
        handle_connection,
        HOST,
        PORT,
        max_size=2 * 1024 * 1024,
        ping_interval=None,  # P0: avoid false disconnects during embedded audio work
        process_request=gateway_http_request,
    ):
        info_task = asyncio.create_task(info_skill_poll_loop())
        gold_task = asyncio.create_task(gold_quote_loop())
        display_task = asyncio.create_task(display_schedule_loop())
        display_reconcile_task = asyncio.create_task(display_reconcile_loop())
        reminder_task = asyncio.create_task(reminder_dispatch_loop())
        kitchen_timer_task = asyncio.create_task(kitchen_timer_loop())
        notification_listener_task = asyncio.create_task(openclaw_voice_session_listener_loop())
        try:
            await asyncio.Future()
        finally:
            service_tasks = [
                info_task, gold_task, display_task, display_reconcile_task,
                reminder_task, kitchen_timer_task, notification_listener_task,
            ]
            for task in service_tasks:
                task.cancel()
            transport_task.cancel()
            if OPENCLAW_TRANSPORT_MANAGER is not None:
                await OPENCLAW_TRANSPORT_MANAGER.close()
            await asyncio.gather(*service_tasks, transport_task, return_exceptions=True)


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
