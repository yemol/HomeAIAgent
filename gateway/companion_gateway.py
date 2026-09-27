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
import base64
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sqlite3
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

from PIL import Image, ImageDraw, ImageFont, ImageOps
import httpx
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.datastructures import Headers
from websockets.http11 import Response
from dotenv import load_dotenv

from openclaw_transport import OpenClawTransportConfig, OpenClawTransportManager
from kitchen_menu import KitchenMenuError, ensure_recipe_prep_first, load_kitchen_menu, match_recipe, parse_kitchen_menu, recipe_for
from core.session import ClientSession
from context.followup import build_followup_judge_prompt, parse_followup_decision

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

# A5.0 selective follow-up. No background timer is created: expiry is checked
# only when a candidate arrives, so the feature adds no idle runtime task.
FOLLOWUP_TIMEOUT_SEC = max(3.0, min(30.0, float(
    os.getenv("HOMEAI_FOLLOWUP_TIMEOUT_SEC", "10")
)))
FOLLOWUP_AUDIO_FENCE_SEC = max(0.0, min(2.0, float(
    os.getenv("HOMEAI_FOLLOWUP_AUDIO_FENCE_SEC", "0.5")
)))
FOLLOWUP_JUDGE_TIMEOUT_SEC = max(2.0, min(30.0, float(
    os.getenv("HOMEAI_FOLLOWUP_JUDGE_TIMEOUT_SEC", "12")
)))
FOLLOWUP_JUDGE_CLEANUP_TIMEOUT_SEC = max(0.5, min(5.0, float(
    os.getenv("HOMEAI_FOLLOWUP_JUDGE_CLEANUP_TIMEOUT_SEC", "2")
)))
FOLLOWUP_JUDGE_SESSION_CLEANUP = (
    os.getenv("HOMEAI_FOLLOWUP_JUDGE_SESSION_CLEANUP", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)

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
KITCHEN_UI_VERSION = "A3.0b FIX1 R50.10 · 小K SHOPPING IDENTITY MATCH FIX"
# Baseline compatibility marker: A3.0b FIX1 R49 · 小K COOKING COCKPIT
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
FOOD_DB_FILE = Path(os.getenv("HOMEAI_FOOD_DB_FILE", str(HOMEAI_DATA_DIR / "food_inventory.sqlite3"))).expanduser()
FOOD_DEFAULT_PEOPLE = max(1, int(os.getenv("HOMEAI_FOOD_DEFAULT_PEOPLE", "3")))
KITCHEN_PROGRESS_STATE_FILE = HOMEAI_DATA_DIR / "kitchen_progress.json"
KITCHEN_PREP_STATE_FILE = HOMEAI_DATA_DIR / "kitchen_prep_checklist.json"
KITCHEN_SHOPPING_STATE_FILE = HOMEAI_DATA_DIR / "kitchen_shopping_checklist.json"
KITCHEN_TODAY_EXTRA_FILE = HOMEAI_DATA_DIR / "kitchen_today_extra_recipes.json"
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
            "version": 3,
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
    # Food photo/order scan uses a dedicated one-shot upload on the same Kitchen WS.
    food_scan_receiving: bool = False
    food_scan_processing: bool = False
    food_scan_request_id: str = ""
    food_scan_source: str = ""
    food_scan_mime: str = "image/jpeg"
    food_scan_image: bytearray = field(default_factory=bytearray)


@dataclass
class KitchenTimer:
    timer_id: str
    dish: str
    step: int
    duration_sec: int
    kind: str = "recipe"  # recipe | standalone
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
KITCHEN_PICKER_RECOMMENDATIONS: dict[str, dict[str, Any]] = {}
KITCHEN_CURRENT_STATE: dict[str, Any] = {
    "screen": "idle",
    "date": "",
    "dish": "",
    "step": 0,
}
# Per-day, per-dish last viewed step. Gateway-owned so returning to a dish
# continues where the cook left off, even after an iPad page reload or Gateway restart.
KITCHEN_RECIPE_PROGRESS: dict[str, dict[str, int]] = {}
# Per-day unified prep checklist. Values are stable task IDs checked by the user.
KITCHEN_PREP_CHECKED: dict[str, set[str]] = {}
KITCHEN_SHOPPING_CHECKED: dict[str, set[str]] = {}
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

# KitchenTerminal media-audio state. Audio is synthesized by the Gateway and
# played through one persistent HTMLAudioElement so iPadOS system media routing
# (including AirPlay/HomePod) can own the output path. The latest event remains
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


# Home Food A0.1. The first real-iPad baseline deliberately keeps the data model
# small: family-use units (份 / 个 / 盒 / 瓶 / ...), plus state-only pantry items.
# Recognition/OCR will be layered on top later; the inventory/event store is
# already Gateway-owned so the iPad can be replaced without losing data.
FOOD_ALLOWED_STATUS = {"充足", "一般", "快没了"}
FOOD_ALLOWED_PRIORITY = {"这两天", "本周", "暂不着急"}
FOOD_SCAN_MAX_BYTES = max(1024 * 1024, int(os.getenv("HOMEAI_FOOD_SCAN_MAX_BYTES", str(12 * 1024 * 1024))))
FOOD_SCAN_MAX_SIDE = max(800, int(os.getenv("HOMEAI_FOOD_SCAN_MAX_SIDE", "2200")))
FOOD_SCAN_TIMEOUT_SEC = max(30.0, float(os.getenv("HOMEAI_FOOD_SCAN_TIMEOUT_SEC", "120")))


def _food_db() -> sqlite3.Connection:
    FOOD_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FOOD_DB_FILE), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS food_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            category TEXT NOT NULL DEFAULT '其他',
            unit TEXT NOT NULL DEFAULT '份',
            quantity REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            storage TEXT NOT NULL DEFAULT '',
            priority_window TEXT NOT NULL DEFAULT '暂不着急',
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS food_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            event_type TEXT NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    # R50.9: food_events used to be linked to inventory rows only by the
    # visible name. Renaming an item therefore made its earlier add events
    # unreachable and the displayed intake time disappeared. Add a stable
    # item_id identity and backfill legacy history in-place. No duplicate
    # timestamp field is introduced.
    event_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(food_events)").fetchall()}
    if "item_id" not in event_columns:
        conn.execute("ALTER TABLE food_events ADD COLUMN item_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_food_events_item_type_created ON food_events(item_id,event_type,created_at)"
    )

    # Directly link events whose stored name still equals the current item name.
    conn.execute(
        """UPDATE food_events
              SET item_id=(SELECT fi.id FROM food_items fi WHERE fi.name=food_events.name COLLATE NOCASE)
            WHERE item_id IS NULL
              AND EXISTS(SELECT 1 FROM food_items fi WHERE fi.name=food_events.name COLLATE NOCASE)"""
    )

    # Then walk rename history backwards. A rename event stores "old → new"
    # in note and uses the new visible name. Processing newest to oldest lets
    # multi-step renames (A → B → C) inherit the same immutable item id.
    current_ids = {str(row["name"]).casefold(): int(row["id"]) for row in conn.execute("SELECT id,name FROM food_items").fetchall()}
    rename_rows = conn.execute(
        "SELECT id,item_id,name,note FROM food_events WHERE event_type='rename' ORDER BY id DESC"
    ).fetchall()
    for rename_row in rename_rows:
        note = str(rename_row["note"] or "")
        if "→" not in note:
            continue
        old_name, noted_new_name = [part.strip() for part in note.split("→", 1)]
        if not old_name:
            continue
        item_id = int(rename_row["item_id"]) if rename_row["item_id"] is not None else None
        if item_id is None:
            for candidate in (str(rename_row["name"] or ""), noted_new_name):
                if candidate and candidate.casefold() in current_ids:
                    item_id = current_ids[candidate.casefold()]
                    break
        if item_id is None:
            continue
        conn.execute("UPDATE food_events SET item_id=? WHERE id=?", (item_id, int(rename_row["id"])))
        conn.execute(
            "UPDATE food_events SET item_id=? WHERE item_id IS NULL AND id<? AND name=? COLLATE NOCASE",
            (item_id, int(rename_row["id"]), old_name),
        )
        current_ids[old_name.casefold()] = item_id
        if noted_new_name:
            current_ids[noted_new_name.casefold()] = item_id
    return conn


def _food_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _food_clean_name(value: str) -> str:
    name = re.sub(r"\\s+", " ", str(value or "").strip())
    if not name or len(name) > 60:
        raise ValueError("食材名称无效")
    return name


def _food_add(name: str, amount: float, unit: str, category: str, source: str = "") -> None:
    name = _food_clean_name(name)
    unit = str(unit or "份").strip() or "份"
    category = str(category or "其他").strip() or "其他"
    source = str(source or "").strip()
    if unit == "状态":
        raise ValueError("状态型食材请使用状态更新")
    if amount <= 0 or amount > 9999:
        raise ValueError("数量必须大于0")
    now = _food_now()
    with _food_db() as conn:
        row = conn.execute("SELECT id, unit, quantity FROM food_items WHERE name=?", (name,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO food_items(name,category,unit,quantity,status,source,priority_window,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (name, category, unit, float(amount), "", source, "暂不着急", now),
            )
            item_id = int(cur.lastrowid)
        else:
            item_id = int(row["id"])
            # If the household changes a natural unit, use the newest unit and
            # still preserve the accumulated numeric count for this A0.1 phase.
            conn.execute(
                "UPDATE food_items SET category=?,unit=?,quantity=MAX(0,quantity+?),source=?,updated_at=? WHERE id=?",
                (category, unit, float(amount), source, now, item_id),
            )
        conn.execute(
            "INSERT INTO food_events(item_id,event_type,name,amount,unit,category,source,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (item_id, "add", name, float(amount), unit, category, source, now),
        )


def _food_consume(name: str, amount: float) -> None:
    name = _food_clean_name(name)
    if amount <= 0 or amount > 9999:
        raise ValueError("数量必须大于0")
    now = _food_now()
    with _food_db() as conn:
        row = conn.execute("SELECT id,category,unit,quantity FROM food_items WHERE name=?", (name,)).fetchone()
        if row is None or str(row["unit"]) == "状态":
            raise ValueError("没有找到可扣减的食材")
        actual = min(float(amount), max(0.0, float(row["quantity"])))
        conn.execute("UPDATE food_items SET quantity=MAX(0,quantity-?),updated_at=? WHERE id=?", (actual, now, int(row["id"])))
        conn.execute(
            "INSERT INTO food_events(item_id,event_type,name,amount,unit,category,created_at) VALUES(?,?,?,?,?,?,?)",
            (int(row["id"]), "consume", name, actual, str(row["unit"]), str(row["category"]), now),
        )


def _food_set_status(name: str, status: str, category: str = "佐料/粮油") -> None:
    name = _food_clean_name(name)
    status = str(status or "").strip()
    if status not in FOOD_ALLOWED_STATUS:
        raise ValueError("状态必须是：充足 / 一般 / 快没了")
    now = _food_now()
    with _food_db() as conn:
        conn.execute(
            """INSERT INTO food_items(name,category,unit,quantity,status,source,priority_window,updated_at)
               VALUES(?,?, '状态',0,?,'','暂不着急',?)
               ON CONFLICT(name) DO UPDATE SET category=excluded.category,unit='状态',quantity=0,status=excluded.status,updated_at=excluded.updated_at""",
            (name, str(category or "佐料/粮油"), status, now),
        )
        item_row = conn.execute("SELECT id FROM food_items WHERE name=?", (name,)).fetchone()
        item_id = int(item_row["id"]) if item_row is not None else None
        conn.execute(
            "INSERT INTO food_events(item_id,event_type,name,unit,category,note,created_at) VALUES(?,?,?,?,?,?,?)",
            (item_id, "status", name, "状态", str(category or "佐料/粮油"), status, now),
        )


def _food_set_priority(name: str, priority: str) -> None:
    name = _food_clean_name(name)
    priority = str(priority or "").strip()
    if priority not in FOOD_ALLOWED_PRIORITY:
        raise ValueError("建议窗口无效")
    now = _food_now()
    with _food_db() as conn:
        row = conn.execute("SELECT id FROM food_items WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError("没有找到这个食材")
        item_id = int(row["id"])
        cur = conn.execute("UPDATE food_items SET priority_window=?,updated_at=? WHERE id=?", (priority, now, item_id))
        if cur.rowcount <= 0:
            raise ValueError("没有找到这个食材")
        conn.execute(
            "INSERT INTO food_events(item_id,event_type,name,note,created_at) VALUES(?,?,?,?,?)",
            (item_id, "priority", name, priority, now),
        )


def _food_rename(old_name: str, new_name: str) -> None:
    old_name = _food_clean_name(old_name)
    new_name = _food_clean_name(new_name)
    if old_name.casefold() == new_name.casefold():
        if old_name != new_name:
            now = _food_now()
            with _food_db() as conn:
                cur = conn.execute("UPDATE food_items SET name=?,updated_at=? WHERE name=?", (new_name, now, old_name))
                if cur.rowcount <= 0:
                    raise ValueError("没有找到这个食材")
        return
    now = _food_now()
    with _food_db() as conn:
        row = conn.execute("SELECT id,category,unit FROM food_items WHERE name=?", (old_name,)).fetchone()
        if row is None:
            raise ValueError("没有找到这个食材")
        clash = conn.execute("SELECT id FROM food_items WHERE name=?", (new_name,)).fetchone()
        if clash is not None:
            raise ValueError("已经有同名食材，请换一个名称")
        conn.execute("UPDATE food_items SET name=?,updated_at=? WHERE id=?", (new_name, now, int(row["id"])))
        conn.execute(
            "INSERT INTO food_events(item_id,event_type,name,unit,category,note,created_at) VALUES(?,?,?,?,?,?,?)",
            (int(row["id"]), "rename", new_name, str(row["unit"]), str(row["category"]), f"{old_name} → {new_name}", now),
        )


def _food_edit(old_name: str, new_name: str, quantity: float | None = None, item_id: int | None = None) -> dict[str, Any]:
    """Edit one concrete inventory row and verify persistence before returning.

    R50.4 prefers the immutable SQLite row id instead of using the visible food name
    as the record key.  The name is kept only as a backwards-compatible fallback.
    """
    old_name = _food_clean_name(old_name)
    new_name = _food_clean_name(new_name or old_name)
    if quantity is not None and (quantity < 0 or quantity > 9999):
        raise ValueError("库存数量必须在 0 到 9999 之间")
    now = _food_now()
    with _food_db() as conn:
        if item_id is not None:
            row = conn.execute(
                "SELECT id,name,category,unit,quantity FROM food_items WHERE id=?",
                (int(item_id),),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id,name,category,unit,quantity FROM food_items WHERE name=?",
                (old_name,),
            ).fetchone()
        if row is None:
            raise ValueError("没有找到这个食材，请刷新库存后重试")

        actual_old_name = str(row["name"])
        if actual_old_name.casefold() != new_name.casefold():
            clash = conn.execute("SELECT id FROM food_items WHERE name=?", (new_name,)).fetchone()
            if clash is not None and int(clash["id"]) != int(row["id"]):
                raise ValueError("已经有同名食材，请换一个名称")

        old_qty = float(row["quantity"] or 0)
        unit = str(row["unit"] or "")
        new_qty = old_qty if quantity is None or unit == "状态" else float(quantity)
        cur = conn.execute(
            "UPDATE food_items SET name=?,quantity=?,updated_at=? WHERE id=?",
            (new_name, new_qty, now, int(row["id"])),
        )
        if cur.rowcount != 1:
            raise ValueError("库存修改没有写入，请重试")

        if actual_old_name != new_name:
            conn.execute(
                "INSERT INTO food_events(item_id,event_type,name,unit,category,note,created_at) VALUES(?,?,?,?,?,?,?)",
                (int(row["id"]), "rename", new_name, unit, str(row["category"]), f"{actual_old_name} → {new_name}", now),
            )
        if unit != "状态" and abs(new_qty - old_qty) > 1e-9:
            old_text = str(int(old_qty)) if old_qty.is_integer() else str(round(old_qty, 2))
            new_text = str(int(new_qty)) if new_qty.is_integer() else str(round(new_qty, 2))
            conn.execute(
                "INSERT INTO food_events(item_id,event_type,name,amount,unit,category,source,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(row["id"]), "adjust", new_name, new_qty, unit, str(row["category"]), "库存盘点", f"{old_text} {unit} → {new_text} {unit}", now),
            )

        # Read back from the same transaction so a false-success can never reach the iPad.
        check = conn.execute(
            """SELECT fi.id,fi.name,fi.category,fi.unit,fi.quantity,fi.status,fi.source,fi.storage,fi.priority_window,fi.updated_at,
                      (SELECT MAX(fe.created_at) FROM food_events fe
                       WHERE fe.event_type='add' AND fe.item_id=fi.id) AS last_added_at
                   FROM food_items fi WHERE fi.id=?""",
            (int(row["id"]),),
        ).fetchone()
        if check is None or str(check["name"]) != new_name:
            raise ValueError("库存修改校验失败，请重试")
        return dict(check)


def _food_snapshot() -> dict[str, Any]:
    with _food_db() as conn:
        rows = conn.execute(
            """SELECT fi.id,fi.name,fi.category,fi.unit,fi.quantity,fi.status,fi.source,fi.storage,fi.priority_window,fi.updated_at,
                      (SELECT MAX(fe.created_at) FROM food_events fe
                       WHERE fe.event_type='add' AND fe.item_id=fi.id) AS last_added_at
                   FROM food_items fi ORDER BY fi.category,fi.name"""
        ).fetchall()
        recent = conn.execute(
            "SELECT event_type,name,amount,unit,source,note,created_at FROM food_events ORDER BY id DESC LIMIT 800"
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        unit = str(row["unit"])
        qty = float(row["quantity"] or 0)
        if unit != "状态" and qty <= 0:
            continue
        quantity: int | float = int(qty) if qty.is_integer() else round(qty, 2)
        items.append({
            "id": int(row["id"]), "name": str(row["name"]), "category": str(row["category"]), "unit": unit,
            "quantity": quantity, "status": str(row["status"] or ""), "source": str(row["source"] or ""),
            "storage": str(row["storage"] or ""), "priority_window": str(row["priority_window"] or "暂不着急"),
            "updated_at": str(row["updated_at"] or ""), "last_added_at": str(row["last_added_at"] or ""),
        })
    events = [dict(x) for x in recent]
    needs_attention = [x for x in items if x["unit"] == "状态" and x["status"] == "快没了"]
    priority = [x for x in items if x["unit"] != "状态" and x["priority_window"] in {"这两天", "本周"}]
    priority_rank = {"这两天": 0, "本周": 1, "暂不着急": 2}
    recommended = sorted(
        [x for x in items if x["unit"] != "状态"],
        key=lambda x: (priority_rank.get(str(x.get("priority_window") or "暂不着急"), 3), str(x.get("updated_at") or ""), str(x.get("name") or "")),
    )
    return {
        "ok": True,
        "default_people": FOOD_DEFAULT_PEOPLE,
        "items": items,
        "priority": priority,
        "recommended": recommended,
        "needs_attention": needs_attention,
        "events": events,
        "updated_at": _food_now(),
    }


def _food_scan_image_payload(raw: bytes) -> tuple[bytes, str]:
    if not raw:
        raise ValueError("没有收到图片")
    if len(raw) > FOOD_SCAN_MAX_BYTES:
        raise ValueError("图片太大，请选择 12MB 以内的图片")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            scale = min(1.0, float(FOOD_SCAN_MAX_SIDE) / max(w, h, 1))
            if scale < 1.0:
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=84, optimize=True)
            data = out.getvalue()
    except Exception as exc:
        raise ValueError("图片格式无法读取，请换一张照片或截图") from exc
    return data, "image/jpeg"


def _food_scan_ocr_image_payload(raw: bytes) -> bytes:
    """Preserve text resolution for long order screenshots instead of shrinking by total image height."""
    if not raw:
        raise ValueError("没有收到图片")
    if len(raw) > FOOD_SCAN_MAX_BYTES:
        raise ValueError("图片太大，请选择 12MB 以内的图片")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            # Order screenshots are often tall. Keep readable width and only cap extreme dimensions.
            max_w, max_h = 1800, 10000
            scale = min(1.0, float(max_w) / max(w, 1), float(max_h) / max(h, 1))
            if scale < 1.0:
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=92, optimize=True)
            return out.getvalue()
    except Exception as exc:
        raise ValueError("图片格式无法读取，请换一张照片或截图") from exc


def _food_scan_extract_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        raise ValueError("识别结果为空")
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    try:
        body = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("识别结果不是有效 JSON")
        body = json.loads(value[start:end + 1])
    if not isinstance(body, dict):
        raise ValueError("识别结果格式不正确")
    return body


def _food_scan_normalize_item(row: Any, source: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    name = re.sub(r"\s+", " ", str(row.get("name") or "").strip())[:60]
    if not name:
        return None
    category = str(row.get("category") or "其他").strip() or "其他"
    allowed_categories = {"肉类", "海鲜", "蔬菜", "蛋类", "奶制品", "包装食品", "主食", "佐料/粮油", "其他"}
    if category not in allowed_categories:
        category = "其他"
    mode = str(row.get("mode") or "quantity").strip().lower()
    unit = str(row.get("unit") or "份").strip() or "份"
    raw = str(row.get("raw") or row.get("original") or "").strip()[:120]
    try:
        confidence = max(0.0, min(1.0, float(row.get("confidence", 0.8))))
    except (TypeError, ValueError):
        confidence = 0.8
    if mode == "status" or unit == "状态" or category == "佐料/粮油":
        return {"name": name, "category": category, "mode": "status", "status": "充足", "unit": "状态", "amount": 0, "source": source, "raw": raw, "confidence": round(confidence, 2)}
    try:
        amount = float(row.get("amount") or 1)
    except (TypeError, ValueError):
        amount = 1.0
    amount = max(0.25, min(9999.0, amount))
    if amount.is_integer():
        amount = int(amount)
    allowed_units = {"份", "个", "盒", "瓶", "包", "杯", "块", "根", "颗", "袋"}
    if unit not in allowed_units:
        unit = "份"
    return {"name": name, "category": category, "mode": "quantity", "status": "", "unit": unit, "amount": amount, "source": source, "raw": raw, "confidence": round(confidence, 2)}


async def _food_scan_openclaw_json(messages: list[dict[str, Any]], *, category: str) -> tuple[dict[str, Any], str]:
    session_key = _new_info_session_key(category)
    headers = {"Content-Type": "application/json", "x-openclaw-session-key": session_key}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"
    payload = {"model": OPENCLAW_MODEL, "stream": False, "messages": messages}
    try:
        async with _openclaw_http_client(FOOD_SCAN_TIMEOUT_SEC) as client:
            response = await _post_with_retry(
                client,
                f"{OPENCLAW_BASE_URL}/v1/chat/completions",
                attempts=1,
                headers=headers,
                json=payload,
            )
            body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("OpenClaw 没有返回识别结果")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        text = str(content or "")
        return _food_scan_extract_json(text), text
    finally:
        try:
            await _cleanup_openclaw_info_session(session_key, category=category, attempt=1)
        except Exception:
            pass


def _food_scan_rows_to_result(parsed: dict[str, Any], source: str) -> dict[str, Any]:
    rows = parsed.get("items") or []
    if not isinstance(rows, list):
        rows = []
    items: list[dict[str, Any]] = []
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows[:40]:
        item = _food_scan_normalize_item(row, source)
        if not item:
            continue
        key = (str(item.get("name") or "").casefold(), str(item.get("mode") or "quantity"), str(item.get("unit") or "份"))
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
            continue
        current = merged[key]
        if item.get("mode") == "quantity":
            total = float(current.get("amount") or 0) + float(item.get("amount") or 0)
            current["amount"] = int(total) if total.is_integer() else round(total, 2)
        current["confidence"] = round(max(float(current.get("confidence") or 0), float(item.get("confidence") or 0)), 2)
        raws = [str(current.get("raw") or "").strip(), str(item.get("raw") or "").strip()]
        current["raw"] = " / ".join(x for i, x in enumerate(raws) if x and x not in raws[:i])[:120]
    items = [merged[key] for key in order]
    return {"ok": True, "items": items, "note": str(parsed.get("note") or "")[:240]}


async def _food_scan_ensure_ocr_binary() -> Path | None:
    """Build the tiny macOS Vision OCR helper once and cache it outside the replaceable gateway tree."""
    if sys.platform != "darwin":
        return None
    source = BASE_DIR / "food_ocr.swift"
    if not source.exists():
        return None
    HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
    binary = HOMEAI_DATA_DIR / "food_ocr_macos"
    try:
        if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime and os.access(binary, os.X_OK):
            return binary
    except OSError:
        pass
    commands = [
        ("/usr/bin/xcrun", "swiftc", "-O", str(source), "-o", str(binary)),
        ("/usr/bin/swiftc", "-O", str(source), "-o", str(binary)),
    ]
    for command in commands:
        if not Path(command[0]).exists():
            continue
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode == 0 and binary.exists():
                try:
                    os.chmod(binary, 0o755)
                except OSError:
                    pass
                print("[FOOD-OCR] macOS Vision helper ready")
                return binary
            msg = (err or out).decode("utf-8", "ignore").strip().replace("\n", " ")[:240]
            print(f"[FOOD-OCR-WARN] helper build failed rc={proc.returncode} {msg}")
        except Exception as exc:
            print(f"[FOOD-OCR-WARN] helper build failed {type(exc).__name__}: {exc}")
    return None


async def _food_scan_macos_ocr(image: bytes) -> str:
    binary = await _food_scan_ensure_ocr_binary()
    if binary is None:
        return ""
    tmp = HOMEAI_DATA_DIR / f"food_scan_ocr_{uuid.uuid4().hex}.jpg"
    try:
        tmp.write_bytes(image)
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=45)
        if proc.returncode != 0:
            msg = err.decode("utf-8", "ignore").strip().replace("\n", " ")[:240]
            print(f"[FOOD-OCR-WARN] OCR failed rc={proc.returncode} {msg}")
            return ""
        text = out.decode("utf-8", "ignore").strip()
        # Order screenshots contain lots of UI chrome. A tiny result is not useful enough to parse.
        if len(re.sub(r"\s+", "", text)) < 8:
            return ""
        print(f"[FOOD-OCR] extracted chars={len(text)} lines={len(text.splitlines())}")
        return text[:24000]
    except Exception as exc:
        print(f"[FOOD-OCR-WARN] {type(exc).__name__}: {exc}")
        return ""
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


async def _food_scan_from_ocr_text(ocr_text: str, *, source: str) -> dict[str, Any]:
    prompt = f"""你是 Home Food OS 的订单/小票食材入库解析器。家庭默认 3 人。来源：{source}。
下面是 macOS Vision 从用户真实订单截图/小票中提取的 OCR 文字。请从中找出用户实际购买的食品与食材。

必须遵守：
1. 只识别实际购买的食品/食材。忽略店铺名、地址、配送费、运费、优惠券、红包、会员、价格合计、支付方式、按钮、推荐商品、广告和日用品。
2. 必须完整扫描整个订单/小票商品区，尽量列出其中每一种实际购买的食品/食材，不要只返回第一项；最多返回 30 种。无法确认是不是已购买的商品时宁可不列。
3. 同一商品被 OCR 拆成多行时要合并；“x2 / ×2 / 2件 / 数量2”等要反映到数量；相同商品重复出现时合并数量。
4. 肉类、海鲜：转换为家庭“份”。约 300–700g 可视为 1份（3人烧一次），1kg 左右通常约 2份；如果只有包装数量没有重量，一包/一盒通常先按1份并降低 confidence。
5. 蔬菜：优先转换成“份”；番茄、玉米、土豆等有明确自然数量时可用个/根/颗。鸡蛋必须按“个”。
6. 奶制品和包装食品优先用盒/瓶/杯/包等自然单位。
7. 酱油、醋、料酒、盐、糖、食用油、米、面等佐料/基础粮油：mode=status、unit=状态、status=充足。
8. name 使用家庭里的简洁名称；OCR 原商品文字放 raw。不要因为促销词制造额外商品。
9. confidence 为 0~1。数量/商品归属不确定时降低 confidence。
10. 白米饭不是采购商品时不要凭空加入。

只输出 JSON，不要 markdown，不要解释：
{{"items":[{{"name":"牛腩","category":"肉类","mode":"quantity","amount":1,"unit":"份","status":"","raw":"澳洲谷饲牛腩块480g x1","confidence":0.96}}],"note":""}}

OCR文字如下：
---
{ocr_text}
---
"""
    parsed, content = await _food_scan_openclaw_json(
        [{"role": "user", "content": prompt}],
        category="food-scan-ocr",
    )
    result = _food_scan_rows_to_result(parsed, source)
    if not result["items"]:
        note = str(parsed.get("note") or "")[:160]
        print(f"[FOOD-SCAN-WARN] OCR parse returned 0 items source={source} chars={len(content)} note={note!r}")
    return result


async def _food_scan_from_vision(image: bytes, mime: str, *, source: str) -> dict[str, Any]:
    encoded = base64.b64encode(image).decode("ascii")
    prompt = f"""你是 Home Food OS 的家庭食材视觉入库识别器。家庭默认 3 人。这张图片来源是：{source or '未知'}。
请真正查看附带图片并识别其中的食品/食材。图片可能是买回来的食材照片，也可能是订单截图或超市小票。

必须遵守：
1. 这是“批量入库”识别。必须先完整查看整张图片，再按从左到右、从上到下的方式盘点所有能看到的食品/食材；不要识别到一个就停止。最多返回 20 种不同食品/食材。忽略配送费、购物袋、优惠券、日用品。
2. 同一种食材出现多个时合并为一项，并尽量统计可见数量；不同食材必须分别返回。被部分遮挡但仍能可靠辨认的也可以列出并降低 confidence。不要凭空补商品；看不清宁可不列。
3. 肉类、海鲜优先转换成“份”，蔬菜优先“份”；番茄、玉米、土豆等能数清时可以用个/根/颗；鸡蛋按“个”，包装食品按自然单位。
4. 佐料/米面油使用 mode=status、unit=状态、status=充足。
5. 商品名家庭化，原始文字放 raw。
6. 如果你实际上无法访问/查看这张图片，请不要假装识别，返回 items=[] 且 note="VISION_UNAVAILABLE"。

只输出 JSON，不要 markdown，不要解释：
{{"items":[{{"name":"牛腩","category":"肉类","mode":"quantity","amount":1,"unit":"份","status":"","raw":"澳洲谷饲牛腩块480g","confidence":0.95}}],"note":""}}
"""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"}},
        ],
    }]
    parsed, content = await _food_scan_openclaw_json(messages, category="food-scan-vision")
    result = _food_scan_rows_to_result(parsed, source)
    if not result["items"]:
        note = str(parsed.get("note") or "")[:160]
        print(f"[FOOD-SCAN-WARN] vision returned 0 items source={source} chars={len(content)} note={note!r}")
    return result


async def _food_scan_with_openclaw(raw: bytes, *, source: str) -> dict[str, Any]:
    image, mime = _food_scan_image_payload(raw)
    source = str(source or "菜市场")
    ocr_image = _food_scan_ocr_image_payload(raw)

    # Text-heavy sources should not pay the cost/reliability penalty of a pure vision pass.
    if source in {"网上APP", "超市"}:
        ocr_text = await _food_scan_macos_ocr(ocr_image)
        if ocr_text:
            try:
                result = await _food_scan_from_ocr_text(ocr_text, source=source)
                if result.get("items"):
                    result["pipeline"] = "ocr"
                    return result
            except Exception as exc:
                print(f"[FOOD-SCAN-WARN] OCR pipeline failed {type(exc).__name__}: {exc}; fallback=vision")
        else:
            print(f"[FOOD-SCAN-WARN] OCR unavailable/empty source={source}; fallback=vision")

    result = await _food_scan_from_vision(image, mime, source=source)
    if result.get("items"):
        result["pipeline"] = "vision"
        return result

    # Labels/handwritten notes in a market photo can still be recovered with OCR as a last resort.
    if source not in {"网上APP", "超市"}:
        ocr_text = await _food_scan_macos_ocr(ocr_image)
        if ocr_text:
            try:
                fallback = await _food_scan_from_ocr_text(ocr_text, source=source)
                if fallback.get("items"):
                    fallback["pipeline"] = "ocr-fallback"
                    return fallback
            except Exception as exc:
                print(f"[FOOD-SCAN-WARN] OCR fallback failed {type(exc).__name__}: {exc}")

    result["pipeline"] = "vision"
    return result


async def _food_scan_notify(session: KitchenSession, payload: dict[str, Any]) -> None:
    try:
        await send_json(session.ws, payload)
    except Exception:
        pass


async def _process_food_scan(session: KitchenSession) -> None:
    if session.food_scan_processing:
        return
    session.food_scan_processing = True
    request_id = session.food_scan_request_id or ("fs-" + uuid.uuid4().hex[:12])
    raw = bytes(session.food_scan_image)
    source = session.food_scan_source or "菜市场"
    started = time.perf_counter()
    try:
        if len(raw) < 128:
            raise RuntimeError("图片数据太少，请重新拍一张")
        result = await _food_scan_with_openclaw(raw, source=source)
        result.update({"type": "kitchen.food.scan.result", "request_id": request_id, "source": source})
        print(f"[FOOD-SCAN] done id={request_id} source={source} pipeline={result.get('pipeline','unknown')} items={len(result.get('items') or [])} ms={int((time.perf_counter()-started)*1000)}")
        await _food_scan_notify(session, result)
    except Exception as exc:
        message = str(exc)[:240] or type(exc).__name__
        print(f"[FOOD-SCAN-ERROR] id={request_id} {type(exc).__name__}: {message}")
        _log_traceback()
        await _food_scan_notify(session, {"type": "kitchen.food.scan.error", "request_id": request_id, "ok": False, "message": message})
    finally:
        session.food_scan_processing = False
        session.food_scan_receiving = False
        session.food_scan_image.clear()


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



def _new_followup_judge_session_key() -> str:
    return (
        f"agent:{OPENCLAW_INFO_AGENT_ID}:"
        f"homeai-followup-judge-{uuid.uuid4().hex}"
    )


def _is_safe_followup_judge_session_key(session_key: str) -> bool:
    prefix = f"agent:{OPENCLAW_INFO_AGENT_ID}:homeai-followup-judge-"
    return bool(
        session_key.startswith(prefix)
        and len(session_key) >= len(prefix) + 32
        and "openai-user:" not in session_key
        and "home-ai-agent:main" not in session_key
    )


async def _cleanup_followup_judge_session(session_key: str) -> None:
    if not FOLLOWUP_JUDGE_SESSION_CLEANUP:
        return
    if not _is_safe_followup_judge_session_key(session_key):
        print(f"[FOLLOWUP-JUDGE-WARN] cleanup refused unsafe key={session_key!r}")
        return
    try:
        await _openclaw_gateway_rpc(
            "sessions.delete",
            {"key": session_key, "deleteTranscript": True},
            timeout=FOLLOWUP_JUDGE_CLEANUP_TIMEOUT_SEC,
        )
    except Exception as exc:
        # Judge cleanup failure must never mutate or block the primary voice
        # session. The candidate remains fail-closed regardless.
        print(
            f"[FOLLOWUP-JUDGE-WARN] cleanup failed "
            f"error={type(exc).__name__}: {exc}"
        )


async def _judge_followup_context(
    session: ClientSession,
    transcript: str,
) -> tuple[str, str]:
    turns = session.followup.context_payload()
    if not turns:
        return "ignore", "no_followup_context"

    prompt = build_followup_judge_prompt(turns, transcript)
    session_key = _new_followup_judge_session_key()
    headers = {
        "Content-Type": "application/json",
        "x-openclaw-session-key": session_key,
    }
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"

    payload = {
        "model": OPENCLAW_MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        async with _openclaw_http_client(FOLLOWUP_JUDGE_TIMEOUT_SEC) as client:
            response = await _post_with_retry(
                client,
                f"{OPENCLAW_BASE_URL}/v1/chat/completions",
                attempts=1,
                headers=headers,
                json=payload,
            )
            body = response.json()
        choices = body.get("choices") or []
        if not choices:
            return "ignore", "judge_no_choices"
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        return parse_followup_decision(content)
    except Exception as exc:
        # Fail closed. A Context Judge outage must never allow an unrelated
        # no-wake utterance into the stable OpenClaw conversation.
        print(
            f"[FOLLOWUP-JUDGE-WARN] fail-closed "
            f"error={type(exc).__name__}: {exc}"
        )
        return "ignore", f"judge_error:{type(exc).__name__}"
    finally:
        await _cleanup_followup_judge_session(session_key)


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
    is_followup = session.ptt_trigger == "follow_up"
    followup_validated = not is_followup
    pending_glass2_target: bool | None = None
    try:
        if is_followup and not session.followup_candidate_authorized:
            print("[FOLLOWUP] stale/unarmed candidate ignored before ASR")
            session.processing = False
            await send_state(session.ws, "idle")
            return

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

        if is_followup:
            decision, reason = await _judge_followup_context(session, transcript)
            print(
                f"[FOLLOWUP-JUDGE] decision={decision} reason={reason!r} "
                f"text={transcript!r}"
            )
            if decision != "continue":
                session.followup.reset_chain("judge_ignore")
                session.followup_candidate_authorized = False
                session.processing = False
                await send_state(session.ws, "idle")
                return
            followup_validated = True

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
            # Kitchen audio playback is owned by the iPad and has no device-side
            # playback.done ACK on this companion socket, so do not guess when a
            # no-wake window should begin.
            session.followup.reset_chain("kitchen_audio_external_playback")
            print(f"[KITCHEN-AUDIO] routed reply to iPad id={event_id} source={session.device_id}")
        else:
            sink = await send_pcm_to_routed_sink(session, pcm_out, sample_rate)
            print(f"[AUDIO-ROUTE] reply complete source={session.device_id} sink={sink.device_id}")

            # A5.0: only a successfully spoken, user-originated turn may arm the
            # no-wake continuation window. Keep the short history in Gateway
            # memory; the real OpenClaw conversation remains the existing stable
            # per-device session.
            session.followup.record_turn(transcript, answer)
            if FOLLOWUP_AUDIO_FENCE_SEC > 0:
                await asyncio.sleep(FOLLOWUP_AUDIO_FENCE_SEC)
            session.followup.arm(FOLLOWUP_TIMEOUT_SEC)
            if session.followup.is_active():
                await send_json(session.ws, {
                    "type": "followup.arm",
                    "timeout_ms": int(FOLLOWUP_TIMEOUT_SEC * 1000),
                })
                print(
                    f"[FOLLOWUP] armed device={session.device_id} "
                    f"timeout={FOLLOWUP_TIMEOUT_SEC:.1f}s "
                    f"turns={len(session.followup.turns)}"
                )

        session.followup_candidate_authorized = False
        session.processing = False
        await send_state(session.ws, "idle")
        if pending_glass2_target is not None:
            await _apply_glass2_target(session, pending_glass2_target)

    except Exception as exc:
        # A no-wake candidate that fails before Context Judge approval is
        # indistinguishable from noise/ASR trouble from the user's point of
        # view. Fail closed and silently return to idle. Once the Judge has
        # approved it, the utterance is a real user turn and normal error
        # reporting applies.
        if is_followup and not followup_validated:
            print(
                f"[FOLLOWUP-WARN] pre-judge candidate dropped "
                f"error={type(exc).__name__}: {exc}"
            )
            session.followup.reset_chain("candidate_error")
            session.followup_candidate_authorized = False
            session.processing = False
            try:
                await send_state(session.ws, "idle")
            except Exception:
                pass
            return

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
        session.followup_candidate_authorized = False
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
    now = time.time()
    if timer.status == "running":
        remaining_precise = max(0.0, float(timer.ends_at or 0.0) - now)
    elif timer.status == "paused":
        remaining_precise = max(0.0, float(timer.paused_remaining_sec or 0.0))
    else:
        remaining_precise = 0.0
    return {
        "timer_id": timer.timer_id,
        "dish": timer.dish,
        "step": int(timer.step),
        "duration_sec": int(timer.duration_sec),
        "kind": str(timer.kind or "recipe"),
        "label": "独立计时" if timer.kind == "standalone" else timer.dish,
        "status": timer.status,
        "remaining_sec": int(math.ceil(remaining_precise)),
        "remaining_precise_sec": round(remaining_precise, 3),
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
        payload = {"schema": 2, "progress": KITCHEN_RECIPE_PROGRESS}
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
        schema = int(data.get("schema") or 1) if isinstance(data, dict) else 1
        migrate_prep_step = schema < 2
        raw = data.get("progress") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for date_text, dishes in raw.items():
                if not isinstance(dishes, dict):
                    continue
                clean: dict[str, int] = {}
                for dish, step in dishes.items():
                    try:
                        value = max(0, int(step))
                        clean[str(dish)] = value + (1 if migrate_prep_step else 0)
                    except (TypeError, ValueError):
                        continue
                if clean:
                    KITCHEN_RECIPE_PROGRESS[str(date_text)] = clean
        if migrate_prep_step and KITCHEN_RECIPE_PROGRESS:
            save_kitchen_progress()
            print("[KITCHEN-PROGRESS] migrated schema=1->2 for prep-first recipe steps")
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


def save_kitchen_prep_state() -> bool:
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "checked": {k: sorted(v) for k, v in KITCHEN_PREP_CHECKED.items() if v}}
        tmp = KITCHEN_PREP_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(KITCHEN_PREP_STATE_FILE)
        return True
    except Exception as exc:
        print(f"[KITCHEN-PREP-WARN] save failed: {type(exc).__name__}: {exc}")
        return False


def load_kitchen_prep_state() -> None:
    KITCHEN_PREP_CHECKED.clear()
    if not KITCHEN_PREP_STATE_FILE.exists():
        return
    try:
        data = json.loads(KITCHEN_PREP_STATE_FILE.read_text(encoding="utf-8"))
        raw = data.get("checked") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for date_text, ids in raw.items():
                if isinstance(ids, list):
                    KITCHEN_PREP_CHECKED[str(date_text)] = {str(x) for x in ids if str(x)}
        if len(KITCHEN_PREP_CHECKED) > 45:
            for old in sorted(KITCHEN_PREP_CHECKED)[:-45]:
                KITCHEN_PREP_CHECKED.pop(old, None)
        print(f"[KITCHEN-PREP] loaded dates={len(KITCHEN_PREP_CHECKED)} file={KITCHEN_PREP_STATE_FILE}")
    except Exception as exc:
        print(f"[KITCHEN-PREP-WARN] load failed: {type(exc).__name__}: {exc}")


def _kitchen_prep_task_id(date_text: str, dish: str, index: int, text: str) -> str:
    raw = f"{date_text}\n{dish}\n{index}\n{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:18]


def save_kitchen_shopping_state() -> bool:
    try:
        HOMEAI_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "checked": {k: sorted(v) for k, v in KITCHEN_SHOPPING_CHECKED.items() if v}}
        tmp = KITCHEN_SHOPPING_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(KITCHEN_SHOPPING_STATE_FILE)
        return True
    except Exception as exc:
        print(f"[KITCHEN-SHOPPING-WARN] save failed: {type(exc).__name__}: {exc}")
        return False


def load_kitchen_shopping_state() -> None:
    KITCHEN_SHOPPING_CHECKED.clear()
    if not KITCHEN_SHOPPING_STATE_FILE.exists():
        return
    try:
        data = json.loads(KITCHEN_SHOPPING_STATE_FILE.read_text(encoding="utf-8"))
        raw = data.get("checked") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for date_text, ids in raw.items():
                if isinstance(ids, list):
                    KITCHEN_SHOPPING_CHECKED[str(date_text)] = {str(x) for x in ids if str(x)}
        if len(KITCHEN_SHOPPING_CHECKED) > 45:
            for old in sorted(KITCHEN_SHOPPING_CHECKED)[:-45]:
                KITCHEN_SHOPPING_CHECKED.pop(old, None)
        print(f"[KITCHEN-SHOPPING] loaded dates={len(KITCHEN_SHOPPING_CHECKED)} file={KITCHEN_SHOPPING_STATE_FILE}")
    except Exception as exc:
        print(f"[KITCHEN-SHOPPING-WARN] load failed: {type(exc).__name__}: {exc}")


def _kitchen_shopping_item_id(date_text: str, group: str, text: str) -> str:
    raw = f"{date_text}\n{group}\n{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:18]


def _kitchen_shopping_match_key(value: str) -> str:
    # Normalize presentation only. Ingredient identity is handled separately so
    # “牛肉” does not become equal to a different form such as “牛肉片”.
    return re.sub(r"[\s·•，,。；;：:（）()【】\[\]{}<>《》/\\_-]+", "", str(value or "").casefold())


_KITCHEN_SHOPPING_IDENTITY_ALIASES = {
    # True name synonyms / benign quality variants. Keep this deliberately small:
    # preparation forms (片/丝/丁/块/卷/馅/排/腩...) must remain distinct.
    "西红柿": "番茄",
    "食盐": "盐",
    "土鸡蛋": "鸡蛋",
    "草鸡蛋": "鸡蛋",
    "柴鸡蛋": "鸡蛋",
    "笨鸡蛋": "鸡蛋",
    "无菌蛋": "鸡蛋",
    "无菌鸡蛋": "鸡蛋",
    "可生食鸡蛋": "鸡蛋",
}
_KITCHEN_SHOPPING_QUALITY_PREFIXES = ("有机", "散养", "新鲜", "本地", "国产", "进口")


def _kitchen_shopping_ingredient_name(text: str) -> str:
    """Return the ingredient-name part of a shopping line.

    Shopping lines commonly end in an amount/unit (e.g. “牛肉片 1份”).
    Remove only that trailing presentation suffix; do not strip food-form words.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    amount = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百半]+)"
    unit = r"(?:个|只|份|盒|瓶|杯|包|袋|根|颗|克|千克|公斤|斤|两|毫升|升|g|kg|ml|l)"
    suffix = rf"\s*(?:约|大约|各)?\s*{amount}\s*{unit}?(?:\s*[（(][^）)]*[）)])?\s*$"
    stripped = re.sub(suffix, "", raw, flags=re.IGNORECASE).strip()
    return stripped or raw


def _kitchen_shopping_identity(value: str) -> str:
    """Canonical identity used only for safe shopping/inventory equivalence.

    Exact identity is the default. We normalize a small set of true synonyms and
    non-form quality prefixes, but intentionally preserve cut/preparation forms.
    """
    key = _kitchen_shopping_match_key(value)
    if not key:
        return ""
    key = _KITCHEN_SHOPPING_IDENTITY_ALIASES.get(key, key)
    changed = True
    while changed:
        changed = False
        for prefix in _KITCHEN_SHOPPING_QUALITY_PREFIXES:
            if key.startswith(prefix) and len(key) > len(prefix) + 1:
                key = key[len(prefix):]
                key = _KITCHEN_SHOPPING_IDENTITY_ALIASES.get(key, key)
                changed = True
                break
    return _KITCHEN_SHOPPING_IDENTITY_ALIASES.get(key, key)


def _kitchen_shopping_inventory_badge(item: dict[str, Any]) -> dict[str, Any]:
    unit = str(item.get("unit") or "").strip()
    name = str(item.get("name") or "").strip()
    if unit == "状态":
        status = str(item.get("status") or "").strip() or "有库存"
        return {"name": name, "unit": unit, "status": status, "label": f"库存 {status}"}
    qty = float(item.get("quantity") or 0)
    quantity: int | float = int(qty) if qty.is_integer() else round(qty, 2)
    return {"name": name, "unit": unit, "quantity": quantity, "label": f"库存 {quantity}{unit}"}


def _kitchen_shopping_inventory_match(text: str, inventory_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    shopping_name = _kitchen_shopping_ingredient_name(text)
    shopping_key = _kitchen_shopping_match_key(shopping_name)
    wanted_identity = _kitchen_shopping_identity(shopping_name)
    if not shopping_key or not wanted_identity:
        return None

    # R50.10: never use substring containment for inventory cross-check.
    # It made “牛肉片” inherit stock from “牛肉”. Match canonical identities
    # exactly, preferring an exact displayed-name match when several rows share
    # the same benign alias (e.g. 鸡蛋 / 土鸡蛋).
    best: tuple[int, int, dict[str, Any]] | None = None
    for item in inventory_items:
        name = str(item.get("name") or "").strip()
        item_key = _kitchen_shopping_match_key(name)
        item_identity = _kitchen_shopping_identity(name)
        if not item_key or not item_identity or item_identity != wanted_identity:
            continue
        exact = 1 if item_key == shopping_key else 0
        score = (exact, len(item_key))
        if best is None or score > best[:2]:
            best = (score[0], score[1], item)
    return _kitchen_shopping_inventory_badge(best[2]) if best else None


def _kitchen_shopping_groups_with_state(menu: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    date_text = str(menu.get("date") or _kitchen_today())
    checked = KITCHEN_SHOPPING_CHECKED.setdefault(date_text, set())
    groups: list[dict[str, Any]] = []
    valid_ids: set[str] = set()
    try:
        inventory_items = list(_food_snapshot().get("items") or [])
    except Exception as exc:
        inventory_items = []
        print(f"[KITCHEN-SHOPPING-WARN] inventory cross-check unavailable: {type(exc).__name__}: {exc}")
    for raw_group in menu.get("shopping") or []:
        if not isinstance(raw_group, dict):
            continue
        group_name = str(raw_group.get("name") or "建议购买").strip() or "建议购买"
        out_items: list[dict[str, Any]] = []
        for raw_item in raw_group.get("items") or []:
            text = str(raw_item.get("text") if isinstance(raw_item, dict) else raw_item).strip()
            if not text:
                continue
            item_id = _kitchen_shopping_item_id(date_text, group_name, text)
            valid_ids.add(item_id)
            out_item = {"id": item_id, "text": text, "checked": item_id in checked}
            inventory = _kitchen_shopping_inventory_match(text, inventory_items)
            if inventory:
                out_item["inventory"] = inventory
            out_items.append(out_item)
        if out_items:
            groups.append({"name": group_name, "items": out_items})
    stale = checked - valid_ids
    if stale:
        checked.intersection_update(valid_ids)
        if not checked:
            KITCHEN_SHOPPING_CHECKED.pop(date_text, None)
        save_kitchen_shopping_state()
    return groups, valid_ids


def _kitchen_set_shopping_item(menu: dict[str, Any], item_id: str, done: bool) -> dict[str, Any]:
    date_text = str(menu.get("date") or _kitchen_today())
    _, valid_ids = _kitchen_shopping_groups_with_state(menu)
    key = str(item_id or "").strip()
    if not key or key not in valid_ids:
        raise KitchenMenuError("shopping item not found")
    checked = KITCHEN_SHOPPING_CHECKED.setdefault(date_text, set())
    if done:
        checked.add(key)
    else:
        checked.discard(key)
    if not checked:
        KITCHEN_SHOPPING_CHECKED.pop(date_text, None)
    save_kitchen_shopping_state()
    return _kitchen_shopping_payload(menu)


def _kitchen_prep_payload(menu: dict[str, Any]) -> dict[str, Any]:
    date_text = str(menu.get("date") or _kitchen_today())
    checked = KITCHEN_PREP_CHECKED.setdefault(date_text, set())
    groups: list[dict[str, Any]] = []
    valid_ids: set[str] = set()
    total = 0
    completed = 0
    for raw in menu.get("recipes") or []:
        recipe = ensure_recipe_prep_first(dict(raw))
        dish = str(recipe.get("name") or "").strip()
        if not dish:
            continue
        prep_items = [str(x).strip() for x in (recipe.get("prep_items") or []) if str(x).strip()]
        if not prep_items:
            prep_items = ["确认食材、调味和所需厨具已备齐"]
        tasks: list[dict[str, Any]] = []
        for idx, text in enumerate(prep_items):
            task_id = _kitchen_prep_task_id(date_text, dish, idx, text)
            valid_ids.add(task_id)
            done = task_id in checked
            total += 1
            if done:
                completed += 1
            tasks.append({"id": task_id, "text": text, "done": done})
        groups.append({
            "dish": dish,
            "ingredients": [str(x) for x in (recipe.get("ingredients") or [])],
            "seasoning": [str(x) for x in (recipe.get("seasoning") or [])],
            "tasks": tasks,
            "done": bool(tasks) and all(x["done"] for x in tasks),
        })
    # Remove stale task ids if today's menu changed.
    stale = {x for x in checked if x not in valid_ids}
    if stale:
        checked.difference_update(stale)
        save_kitchen_prep_state()
    all_done = total > 0 and completed == total
    if all_done:
        # Unified prep replaces each recipe's legacy step 0. Once all prep tasks
        # are checked, advance untouched dishes to the first real cooking step.
        for raw in menu.get("recipes") or []:
            recipe = ensure_recipe_prep_first(dict(raw))
            dish = str(recipe.get("name") or "").strip()
            steps = recipe.get("steps") or []
            if dish and len(steps) > 1:
                step, has_progress = _kitchen_progress_get(date_text, dish, len(steps))
                if not has_progress or step == 0:
                    _kitchen_progress_set(date_text, dish, 1, len(steps))
    return {
        "type": "kitchen.show_prep",
        "eyebrow": f"{menu.get('date','')} · 今日菜谱",
        "title": "统一备菜",
        "message": "先把所有菜的备菜一次做完，完成一项就勾一项。",
        "groups": groups,
        "completed": completed,
        "total": total,
        "all_done": all_done,
        "footer": "全部备菜完成后，再分别进入每道菜的正式烹饪步骤",
    }


def _kitchen_set_prep_task(menu: dict[str, Any], task_id: str, done: bool) -> dict[str, Any]:
    date_text = str(menu.get("date") or _kitchen_today())
    # Build once to validate the incoming id against the current menu.
    payload = _kitchen_prep_payload(menu)
    valid = {str(task.get("id") or "") for group in payload.get("groups") or [] for task in group.get("tasks") or []}
    if task_id not in valid:
        raise KitchenMenuError("prep task not found")
    checked = KITCHEN_PREP_CHECKED.setdefault(date_text, set())
    if done:
        checked.add(task_id)
    else:
        checked.discard(task_id)
    save_kitchen_prep_state()
    return _kitchen_prep_payload(menu)


def _kitchen_idle_payload(message: str = "等待逐光发送菜单") -> dict[str, Any]:
    menu = KITCHEN_CURRENT_MENU if KITCHEN_CURRENT_MENU and str(KITCHEN_CURRENT_MENU.get("date") or "") == _kitchen_today() else None
    preview = _kitchen_menu_payload(menu) if menu else {}
    return {
        "type": "kitchen.show_idle",
        "eyebrow": "小K · 首页",
        "title": "小K",
        "message": message,
        "can_pull_today": True,
        "items": preview.get("items") or [],
        "servings": menu.get("servings") if menu else None,
        "estimated_minutes": menu.get("estimated_minutes") if menu else None,
        "date": str(menu.get("date") or "") if menu else "",
        "footer": "今日菜谱是每天做饭的主入口",
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
            "version": 2,
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
        version = int(data.get("version") or 1) if isinstance(data, dict) else 1
        migrate_prep_step = version < 2
        rows = data.get("timers") if isinstance(data, dict) else []
        now = time.time()
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            raw_kind = str(raw.get("kind") or "recipe")
            timer = KitchenTimer(
                timer_id=str(raw.get("timer_id") or ""),
                dish=str(raw.get("dish") or ("独立计时" if raw_kind == "standalone" else "")),
                step=max(0, int(raw.get("step") or 0)) + (1 if migrate_prep_step and raw_kind != "standalone" else 0),
                duration_sec=_kitchen_clamp_timer_seconds(int(raw.get("duration_sec") or KITCHEN_TIMER_MIN_SEC)),
                kind=("standalone" if raw_kind == "standalone" else "recipe"),
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
        if migrate_prep_step and KITCHEN_TIMERS:
            save_kitchen_timers()
            print("[KITCHEN-TIMER] migrated version=1->2 for prep-first recipe steps")
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


def _kitchen_timer_start_standalone(seconds: int) -> KitchenTimer:
    seconds = _kitchen_clamp_timer_seconds(seconds)
    # The idle-page timer is intentionally singular. Replacing it must never
    # disturb recipe timers already running in parallel.
    for old_id, old_timer in list(KITCHEN_TIMERS.items()):
        if old_timer.kind == "standalone":
            KITCHEN_TIMERS.pop(old_id, None)
    now = time.time()
    timer = KitchenTimer(
        timer_id="kt-" + uuid.uuid4().hex[:12],
        dish="独立计时",
        step=0,
        duration_sec=seconds,
        kind="standalone",
        status="running",
        started_at=now,
        ends_at=now + seconds,
        created_at=now,
        updated_at=now,
    )
    KITCHEN_TIMERS[timer.timer_id] = timer
    save_kitchen_timers()
    print(f"[KITCHEN-TIMER] standalone start id={timer.timer_id} sec={seconds}")
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
    if timer.kind == "standalone":
        # Standalone alarms are looped by the iPad's persistent HTMLAudioElement
        # until the user explicitly dismisses them. No one-shot TTS is needed.
        print(f"[KITCHEN-TIMER] standalone alarm armed id={timer.timer_id}")
        return True
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
    prep_items = recipe.get('prep_items') or []
    if prep_items:
        lines += ['', '## 🔪 备菜', ''] + [f'- {x}' for x in prep_items]
    steps = recipe.get('cook_steps') or recipe.get('steps') or []
    hints = recipe.get('cook_step_timers') or recipe.get('step_timers') or []
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
        'footer': '确认后进入今日食材结算，再选择是否保存私房菜',
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
        await _kitchen_speak('确认结束今天的烹饪吗？确认后进入今日食材结算，核对实际消耗，再选择是否保存私房菜。', kind='assistant')
    return delivered


async def _kitchen_confirm_finish(*, speak: bool = False) -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    _kitchen_clear_all_timers()
    KITCHEN_CURRENT_STATE = {'screen': 'day_consumption', 'date': str(menu.get('date') or ''), 'dish': '', 'step': 0}
    delivered = await kitchen_broadcast(_kitchen_day_consumption_payload(menu))
    if speak:
        await _kitchen_speak('进入今日食材结算。请核对今天实际用掉的食材和数量，确认后再选择要不要保存私房菜。', kind='assistant')
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
    KITCHEN_RETURN_IDLE_AT = 0.0
    KITCHEN_CURRENT_STATE = {'screen': 'idle', 'date': '', 'dish': '', 'step': 0}
    msg = ('已保存到私房菜：' + '、'.join(saved) + '。') if saved else ''
    delivered = await kitchen_broadcast(_kitchen_idle_payload(msg + '今日厨房已结束。'))
    if speak:
        spoken = (('已经保存' + '、'.join(saved) + '到私房菜。') if saved else '') + '今天的厨房已经结束。'
        await _kitchen_speak(spoken, kind='assistant')
    return delivered, saved


def _kitchen_alarm_wav() -> bytes:
    """Short two-tone chime for the standalone timer; the browser loops it."""
    sample_rate = 16000
    duration = 1.25
    frames = int(sample_rate * duration)
    pcm = array.array("h")
    windows = ((0.00, 0.18, 880.0), (0.28, 0.46, 880.0), (0.72, 0.92, 660.0))
    for i in range(frames):
        t = i / sample_rate
        value = 0.0
        for start, end, freq in windows:
            if start <= t < end:
                local = (t - start) / max(0.001, end - start)
                env = min(1.0, local / 0.08, (1.0 - local) / 0.12)
                value += 0.30 * max(0.0, env) * math.sin(2.0 * math.pi * freq * t)
        pcm.append(int(max(-1.0, min(1.0, value)) * 32767))
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return out.getvalue()


KITCHEN_STANDALONE_ALARM_WAV = _kitchen_alarm_wav()


def _kitchen_html() -> bytes:
    # KitchenTerminal: HTTP polling is authoritative; WebSocket is an optional
    # fast path. Timers and Q&A recovery state are Gateway-owned.
    html = r'''<!doctype html>
<!-- Compatibility history: A3.0b FIX1 R49 · 小K COOKING COCKPIT | A3.0b FIX1 R49.1 · 小K COCKPIT LAYOUT POLISH | A3.0b FIX1 R49.2 · 小K COCKPIT FLOW POLISH | A3.0b FIX1 R50 · 小K FOOD BATCH INTAKE | A3.0b FIX1 R50.1 · 小K END-DAY FOOD SETTLEMENT | A3.0b FIX1 R50.2 · 小K INVENTORY EDIT FIX | A3.0b FIX1 R50.3 · 小K INVENTORY EDIT SAVE FIX | A3.0b FIX1 R50.4 · 小K INVENTORY EDIT PERSIST FIX | A3.0b FIX1 R50.5 · 小K SHOPPING INVENTORY CROSS-CHECK | A3.0b FIX1 R50.6 · 小K PREP-ONLY BOUNDARY | A3.0b FIX1 R50.7 · 小K PREP NO-HEAT BOUNDARY | A3.0b FIX1 R50.8 · 小K INVENTORY INTAKE DATE FILTER | A3.0b FIX1 R50.9 · 小K INTAKE IDENTITY FIX | __KITCHEN_UI_VERSION__ -->
<!-- Compatibility baseline: __KITCHEN_UI_VERSION__ -->
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>KitchenTerminal __KITCHEN_UI_VERSION__</title>
<style>
:root{color-scheme:light;--bg:#f3f4ef;--card:rgba(255,255,252,.92);--card-solid:#fffefb;--ink:#1c211d;--muted:#747b74;--line:#dde2da;--line-strong:#cdd5cc;--accent:#256b4b;--accent-2:#e5f0e8;--danger:#a13b3b;--danger-soft:#f7e9e7;--warn:#8b6828;--warn-soft:#f6efde;--soft:#ecefe9;--shadow:0 12px 34px rgba(45,58,49,.08);--shadow-sm:0 5px 18px rgba(45,58,49,.06)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;width:100%;height:100%;background:radial-gradient(circle at 20% 0,#fafbf7 0,#f3f4ef 48%,#eef0eb 100%);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;color:var(--ink);overflow:hidden}button,input,select{font:inherit;color:inherit}button{touch-action:manipulation;cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}
#app{height:100%;display:flex;flex-direction:column;padding:calc(env(safe-area-inset-top,0px) + 16px) 18px 12px;gap:10px}header{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:50px}.brand-wrap{min-width:0}.brand-kicker{font-size:12px;letter-spacing:.08em;color:var(--muted);font-weight:700;text-transform:uppercase}.brand{display:block;font-weight:820;font-size:25px;letter-spacing:-.02em;margin-top:1px}.version{font-size:10px;color:var(--muted);margin-left:7px;font-weight:600}.status{font-size:12px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap}.dot{width:8px;height:8px;border-radius:50%;background:var(--danger);box-shadow:0 0 0 4px rgba(161,59,59,.08)}.dot.online{background:var(--accent);box-shadow:0 0 0 4px rgba(37,107,75,.08)}.status-pill{border:1px solid var(--line);background:rgba(255,255,252,.8);border-radius:999px;padding:7px 10px;font-size:11px;font-weight:720;white-space:nowrap}.status-pill.ready{border-color:#b7d1c1;color:var(--accent);background:#f5faf6}.status-pill.wait{color:var(--muted)}.status-pill.bad{border-color:#dab5b2;color:var(--danger);background:#fff8f7}.help-btn,.top-food-btn{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:999px;min-height:34px;padding:7px 12px;font-size:12px;font-weight:760;color:var(--ink);box-shadow:var(--shadow-sm)}.top-food-btn{border-color:#c4d8cb;background:#f2f8f4;color:var(--accent)}.audio-route-btn.wireless{border-color:#9bbbaa;color:var(--accent);background:#f8fbf9}
#timerStrip{display:none;gap:9px;overflow-x:auto;padding:2px 0 2px;white-space:nowrap;scrollbar-width:none}#timerStrip::-webkit-scrollbar{display:none}.timer-chip{border:1px solid var(--line);background:var(--card-solid);border-radius:18px;padding:9px 14px;font-size:28px;line-height:1.05;display:inline-flex;gap:9px;align-items:center;font-weight:760;box-shadow:var(--shadow-sm)}.timer-chip.running{border-color:#abd0bb;background:#f7fbf8}.timer-chip.paused{border-color:#d9c590;background:#fffaf0}.timer-chip.finished{border-color:#d6a5a0;color:var(--danger);font-weight:760;background:#fff8f7}.timer-chip .chip-x{border:0;background:transparent;color:var(--danger);font-size:26px;line-height:1;padding:0 0 1px 4px}.finish-btn{border-color:#d3b3b0!important;color:var(--danger)!important;background:#fff9f8!important}.choice-list{display:grid;gap:10px;margin-top:12px}.choice-row{display:flex;align-items:center;gap:12px;border:1px solid var(--line);background:var(--card-solid);border-radius:18px;padding:15px 16px;font-size:19px;font-weight:680}.choice-row input{width:24px;height:24px}.finish-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px}.finish-actions button{min-height:54px;border-radius:16px;border:1px solid var(--line);background:var(--card-solid);font-size:17px;font-weight:720}.finish-actions .primary{background:var(--accent);border-color:var(--accent);color:#fff}.finish-actions .danger{color:var(--danger);border-color:#d3b3b0;background:#fff9f8}
main{flex:1;min-height:0;display:flex;justify-content:center}.panel{width:100%;max-width:1080px;height:100%;background:var(--card);border:1px solid rgba(215,221,213,.86);border-radius:28px;padding:24px 28px;display:flex;flex-direction:column;min-height:0;box-shadow:var(--shadow);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}.eyebrow{font-size:13px;color:var(--muted);margin-bottom:7px;font-weight:720;letter-spacing:.04em}.title{font-size:40px;font-weight:830;line-height:1.08;letter-spacing:-.035em;margin:0 0 10px}.message{font-size:20px;line-height:1.45;color:#4a504a}.content{flex:1;min-height:0;overflow:auto;padding:2px 2px 6px;scrollbar-width:thin}.menu{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}.menu button,.action{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:20px;padding:18px 20px;text-align:left;font-size:22px;font-weight:720;min-height:78px;box-shadow:var(--shadow-sm);transition:transform .12s ease,border-color .12s ease}.menu button:active,.action:active,.nav button:active{transform:scale(.985)}.menu button{display:flex;flex-direction:column;gap:6px}.menu-progress{font-size:13px;color:var(--accent);font-weight:760}.idle-actions{display:flex;justify-content:center;margin-top:38px}.idle-actions button{appearance:none;border:0;background:var(--accent);color:#fff;border-radius:24px;min-height:104px;min-width:min(100%,440px);width:min(100%,440px);padding:18px 28px;font-size:30px;line-height:1.15;font-weight:830;letter-spacing:.2px;box-shadow:0 14px 28px rgba(37,107,75,.18)}.idle-timer{width:min(100%,560px);margin:24px auto 0;border:1px solid var(--line);background:rgba(255,255,252,.9);border-radius:22px;padding:20px 22px;box-shadow:var(--shadow-sm)}.idle-timer-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.idle-timer-title{font-size:20px;font-weight:780}.idle-timer-note{font-size:13px;color:var(--muted)}.idle-timer-time{font-size:50px;line-height:1;font-weight:830;font-variant-numeric:tabular-nums;letter-spacing:1px;margin:17px 0 16px;text-align:center}.idle-timer-time.finished{color:var(--danger)}.idle-timer-actions,.idle-timer-presets{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.idle-timer-actions button,.idle-timer-presets button{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:14px;min-height:48px;padding:8px;font-size:15px;font-weight:720}.idle-timer-actions button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.idle-timer-actions button.danger{color:#fff;background:var(--danger);border-color:var(--danger)}.done-note{margin-top:18px;color:var(--muted);font-size:16px}.toolbar{display:flex;gap:10px;margin-top:16px}.toolbar .action{flex:1;text-align:center;font-size:16px;min-height:54px;padding:11px}.recipe-meta{font-size:15px;color:var(--muted);margin-bottom:11px;font-weight:650}.step-card{border:1px solid var(--line);background:var(--card-solid);border-radius:24px;padding:23px;margin-top:4px;box-shadow:var(--shadow-sm)}.step-label{font-size:14px;color:var(--accent);font-weight:780;margin-bottom:9px}.step-text{font-size:29px;line-height:1.43;font-weight:680;white-space:pre-line;letter-spacing:-.01em}.tips{margin-top:16px;border-top:1px solid var(--line);padding-top:13px}.tips h3{font-size:14px;margin:0 0 7px;color:var(--muted)}.tips ul{margin:0;padding-left:21px}.tips li{font-size:16px;line-height:1.45;margin:4px 0}.step-timer{margin-top:17px;border:1px solid #bfd2c7;background:#f5faf6;border-radius:19px;padding:15px}.step-timer.finished{border-color:#d7aaaa;background:#fff7f7}.timer-title{font-size:14px;color:var(--muted);font-weight:720}.timer-time{font-size:42px;line-height:1;font-variant-numeric:tabular-nums;font-weight:830;letter-spacing:1px;margin-top:4px}.timer-state{font-size:13px;color:var(--muted);margin-top:5px}.timer-controls{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:13px}.timer-controls button{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:14px;min-height:46px;padding:8px;font-size:14px;font-weight:700}.timer-controls button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.timer-controls button.danger{color:var(--danger)}.nav{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding-top:14px}.nav button{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:16px;padding:12px;font-size:16px;font-weight:700;min-height:52px}.nav button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}.list-group{margin:0 0 18px;background:var(--card-solid);border:1px solid var(--line);border-radius:20px;padding:17px 19px}.list-group h3{font-size:18px;margin:0 0 8px}.list-group ul,.timeline{margin:0;padding-left:23px}.list-group li,.timeline li{font-size:18px;line-height:1.48;margin:6px 0}.timeline li{margin:9px 0}footer{padding-top:1px;text-align:center;font-size:10px;color:#9aa099}
.modal{position:fixed;inset:0;background:rgba(28,33,29,.32);display:none;align-items:center;justify-content:center;padding:18px;z-index:30;backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}.modal.show{display:flex}.modal-card{width:min(450px,94vw);background:var(--card-solid);border-radius:24px;border:1px solid var(--line);padding:22px;box-shadow:0 24px 70px rgba(30,40,33,.2)}.modal-card h2{font-size:23px;margin:0 0 14px}.time-inputs{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center}.time-inputs input{width:100%;font-size:34px;text-align:center;border:1px solid var(--line);border-radius:15px;padding:10px;background:#fff}.time-inputs span{font-size:28px;font-weight:700}.modal-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}.modal-actions button{min-height:48px;border-radius:14px;border:1px solid var(--line);background:#fff;font-size:16px;font-weight:720}.modal-actions .primary{background:var(--accent);border-color:var(--accent);color:#fff}.qa-dock{width:100%;max-width:1080px;margin:0 auto;border:1px solid rgba(215,221,213,.9);background:rgba(255,255,252,.94);border-radius:22px;padding:9px 11px;display:flex;align-items:center;gap:13px;min-height:82px;box-shadow:var(--shadow-sm);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}.qa-btn{appearance:none;border:0;background:var(--accent);color:#fff;border-radius:16px;min-width:210px;min-height:62px;padding:11px 20px;font-size:21px;font-weight:820;box-shadow:0 7px 18px rgba(37,107,75,.16)}.qa-btn.recording{background:var(--danger);box-shadow:0 7px 18px rgba(161,59,59,.16)}.qa-btn.busy{background:#737970;box-shadow:none}.qa-copy{flex:1;min-width:0}.qa-status{font-size:14px;font-weight:760;color:var(--accent)}.qa-transcript{font-size:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px}.qa-answer{font-size:15px;line-height:1.35;margin-top:4px;max-height:44px;overflow:auto}.qa-clear{appearance:none;border:0;background:transparent;color:var(--muted);font-size:24px;line-height:1;padding:6px}.help-list{margin:4px 0 0;padding-left:22px}.help-list li{margin:9px 0;line-height:1.45}.help-note{margin-top:13px;padding:11px 12px;border-radius:13px;background:var(--soft);color:var(--muted);font-size:13px;line-height:1.45}
/* Food A0.1 */
.food-modal{position:fixed;inset:0;display:none;z-index:40;background:rgba(238,241,235,.97);padding:calc(env(safe-area-inset-top,0px) + 16px) 18px calc(env(safe-area-inset-bottom,0px) + 16px);overflow:auto}.food-modal.show{display:block}.food-shell{width:min(100%,1080px);margin:0 auto}.food-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:16px}.food-title{font-size:30px;font-weight:830;letter-spacing:-.03em}.food-sub{font-size:13px;color:var(--muted);margin-top:3px}.food-close{border:1px solid var(--line);background:var(--card-solid);border-radius:999px;min-height:44px;padding:9px 16px;font-weight:760}.food-tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;background:rgba(255,255,252,.72);border:1px solid var(--line);padding:6px;border-radius:20px;margin-bottom:15px}.food-tab{border:0;background:transparent;border-radius:15px;min-height:50px;font-weight:720;color:var(--muted)}.food-tab.active{background:var(--card-solid);color:var(--ink);box-shadow:var(--shadow-sm)}.food-view{display:none}.food-view.active{display:block}.food-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}.food-card{background:var(--card-solid);border:1px solid var(--line);border-radius:24px;padding:19px;box-shadow:var(--shadow-sm)}.food-card h3{margin:0;font-size:18px}.food-card-note{font-size:13px;color:var(--muted);margin-top:4px}.food-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}.food-stat{background:var(--soft);border-radius:17px;padding:13px}.food-stat span{display:block;font-size:12px;color:var(--muted)}.food-stat strong{display:block;margin-top:4px;font-size:20px}.food-list{display:grid;gap:9px;margin-top:13px}.food-row{display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--soft);border-radius:16px;padding:12px 13px}.food-row-main{min-width:0}.food-row-name{font-weight:740}.food-row-meta{font-size:12px;color:var(--muted);margin-top:2px}.food-row-value{font-weight:780;white-space:nowrap}.food-empty{padding:22px 10px;color:var(--muted);font-size:14px;text-align:center}.food-form{display:grid;gap:13px}.food-label{display:grid;gap:6px;font-size:13px;font-weight:700;color:var(--muted)}.food-input,.food-select{width:100%;min-height:50px;border:1px solid var(--line);background:#fff;border-radius:14px;padding:10px 12px;font-size:16px;outline:none}.food-input:focus,.food-select:focus{border-color:#9bbbaa;box-shadow:0 0 0 3px rgba(37,107,75,.08)}.food-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.food-primary{border:0;background:var(--accent);color:#fff;border-radius:16px;min-height:54px;padding:12px 18px;font-weight:800}.food-secondary{border:1px solid var(--line);background:var(--card-solid);border-radius:16px;min-height:50px;padding:10px 14px;font-weight:740}.food-channel-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.food-channel{border:1px solid var(--line);background:#fff;border-radius:16px;min-height:68px;padding:10px;font-weight:720}.food-channel.active{border-color:#aac9b7;background:#f2f8f4;color:var(--accent)}.food-note{border-radius:16px;background:var(--soft);padding:12px 13px;font-size:13px;color:var(--muted);line-height:1.45}.food-event-add{color:var(--accent)}.food-event-consume{color:var(--warn)}.food-event-status{color:#6b5a88}.food-toast{position:fixed;left:50%;bottom:calc(env(safe-area-inset-bottom,0px) + 22px);transform:translateX(-50%);z-index:60;background:#1f2822;color:#fff;border-radius:999px;padding:11px 16px;font-size:14px;font-weight:680;box-shadow:0 10px 30px rgba(0,0,0,.18);display:none}.food-toast.show{display:block}

/* 小K Today Menu Flow R20 */
html,body{background:#f5f4ef}
#app{padding:calc(env(safe-area-inset-top,0px) + 18px) 24px 12px;gap:12px}
.modern-header{width:100%;max-width:1080px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;min-height:76px}.modern-header .brand-wrap{display:flex;flex-direction:column}.modern-header .brand{font-size:42px;font-weight:900;line-height:.95;letter-spacing:-.055em;color:#14221b}.brand-sub{font-size:17px;color:#8a8d87;margin-top:8px;font-weight:600}.modern-head-actions{display:flex;align-items:center;gap:10px}.header-voice{appearance:none;border:1px solid #dce3dc;background:#fffefb;border-radius:25px;min-height:58px;padding:7px 17px 7px 8px;display:flex;align-items:center;gap:11px;box-shadow:0 8px 24px rgba(39,55,45,.06);text-align:left}.header-voice-icon{width:44px;height:44px;border-radius:50%;background:#dfeade;display:flex;align-items:center;justify-content:center;font-size:21px}.header-voice strong{display:block;font-size:16px}.header-voice small{display:block;font-size:11px;color:var(--muted);margin-top:2px}.more-btn{appearance:none;border:1px solid var(--line);background:#fffefb;width:48px;height:48px;border-radius:50%;font-size:19px;letter-spacing:2px;color:#596159}.modern-head-actions>.dot{margin-left:1px}
.utility-card{width:min(460px,94vw)}.utility-head{display:flex;justify-content:space-between;align-items:flex-start;gap:15px}.utility-head h2{margin:0}.utility-sub{font-size:13px;color:var(--muted);margin-top:4px}.utility-close{border:0;background:var(--soft);width:38px;height:38px;border-radius:50%;font-size:23px}.utility-status{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 14px}.utility-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px}.utility-actions .help-btn{border-radius:14px;min-height:48px;box-shadow:none}.utility-version{font-size:10px;color:#a3a7a2;text-align:center;margin-top:17px}
.panel.home-mode{background:transparent;border:0;border-radius:0;box-shadow:none;padding:0;backdrop-filter:none;-webkit-backdrop-filter:none}.panel.home-mode>.eyebrow,.panel.home-mode>.title,.panel.home-mode>.message,.panel.home-mode>#nav{display:none!important}.panel.home-mode>.content{padding:0;overflow:auto}.home-dashboard{display:grid;gap:14px;padding:1px 2px 5px}.home-hero{position:relative;min-height:250px;border-radius:30px;overflow:hidden;background:#eee9df url('/kitchen/assets/home_hero.jpg') right center/57% 100% no-repeat;border:1px solid rgba(219,216,207,.78);box-shadow:0 12px 32px rgba(54,57,50,.07)}.home-hero:before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#fffdf8 0%,#fffdf8 38%,rgba(255,253,248,.84) 49%,rgba(255,253,248,.12) 69%,rgba(255,253,248,0) 100%)}.home-hero-copy{position:relative;z-index:1;width:55%;padding:27px 30px}.home-section-kicker{font-size:14px;font-weight:800;color:#506057;letter-spacing:.02em}.home-hero h2{font-size:44px;line-height:1.08;letter-spacing:-.04em;margin:10px 0 10px;max-width:620px}.home-hero-meta{font-size:17px;color:#737a74;line-height:1.55;min-height:26px;font-weight:620}.home-hero-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:20px}.home-primary,.home-secondary{appearance:none;border-radius:17px;min-height:48px;padding:11px 18px;font-size:15px;font-weight:800}.home-primary{border:0;background:#2c704e;color:white}.home-secondary{border:1px solid #d8ded8;background:rgba(255,255,252,.86)}.home-dish-pills{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px}.home-dish-pill{appearance:none;border:1px solid rgba(220,224,218,.9);background:rgba(255,255,252,.9);border-radius:999px;padding:7px 11px;font-size:12px;font-weight:700}
.home-food-card{position:relative;min-height:220px;border-radius:30px;overflow:hidden;background:linear-gradient(100deg,#e8f0e3 0%,#e2eedc 58%,#d7e6ce 100%);border:1px solid #d6e0d2}.home-food-card:after{content:"";position:absolute;right:-6px;bottom:-8px;width:43%;height:100%;background:url('/kitchen/assets/home_food.jpg') center/cover no-repeat;opacity:.96;mask-image:linear-gradient(90deg,transparent,#000 28%);-webkit-mask-image:linear-gradient(90deg,transparent,#000 28%)}.home-food-copy{position:relative;z-index:1;padding:24px 28px;width:68%}.home-food-title-row{display:flex;align-items:center;gap:14px}.home-food-icon{width:62px;height:62px;border-radius:18px;background:#5f9169;color:white;display:flex;align-items:center;justify-content:center;font-size:29px}.home-food-card h3{font-size:31px;letter-spacing:-.035em;margin:0}.home-food-desc{font-size:14px;color:#6c766c;margin:8px 0 15px}.home-food-stats{font-size:13px;color:#536257;font-weight:700;min-height:20px}.home-food-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:17px}.home-food-actions button{appearance:none;border:1px solid rgba(255,255,255,.78);background:rgba(255,255,252,.78);border-radius:999px;min-height:44px;padding:9px 16px;font-weight:770;font-size:14px;backdrop-filter:blur(6px)}
.home-small-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.home-small-card{appearance:none;border:1px solid #e1e0da;background:#fffefb;border-radius:25px;min-height:123px;padding:20px 22px;text-align:left;display:flex;align-items:center;gap:16px;box-shadow:0 7px 23px rgba(49,54,48,.05)}.home-small-icon{width:52px;height:52px;border-radius:17px;background:#fff2e5;display:flex;align-items:center;justify-content:center;font-size:26px}.home-small-card:nth-child(2) .home-small-icon{background:#eef2e7}.home-small-copy{flex:1;min-width:0}.home-small-title{font-size:24px;font-weight:830}.home-small-sub{font-size:15px;color:var(--muted);margin-top:5px}.home-small-value{font-size:12px;color:var(--accent);font-weight:760;margin-top:10px}.home-small-arrow{font-size:25px;color:#a1a59f}
.home-priority{border:1px solid #e2e1db;background:#fffefb;border-radius:25px;padding:18px 21px;box-shadow:0 7px 23px rgba(49,54,48,.04)}.home-priority-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.home-priority-title{font-size:22px;font-weight:830}.home-priority-note{font-size:14px;color:var(--muted);margin-left:8px;font-weight:500}.home-priority-more{appearance:none;border:0;background:transparent;color:#818681;font-size:13px;font-weight:700}.home-priority-list{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:13px}.home-priority-item{appearance:none;border:0;background:#f6f5f1;border-radius:17px;padding:13px 15px;display:flex;align-items:center;justify-content:space-between;text-align:left;min-height:66px}.home-priority-name{font-size:18px;font-weight:800}.home-priority-meta{font-size:14px;color:var(--muted);margin-top:4px}.home-priority-tag{border-radius:999px;background:#e7efe4;color:#547058;padding:6px 9px;font-size:11px;font-weight:760}.home-priority-tag.week{background:#f7eddb;color:#946b2c}.home-priority-empty{grid-column:1/-1;color:var(--muted);font-size:13px;padding:8px 2px}
.home-bottom-nav{position:sticky;bottom:0;display:grid;grid-template-columns:repeat(4,1fr);gap:5px;background:rgba(250,249,246,.93);border:1px solid rgba(222,222,215,.9);border-radius:24px;padding:6px;backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);box-shadow:0 8px 28px rgba(50,54,49,.08);margin-top:1px}.home-bottom-nav button{appearance:none;border:0;background:transparent;border-radius:18px;min-height:58px;font-size:12px;font-weight:740;color:#7c817c}.home-bottom-nav button.active{background:#edf3eb;color:#2c704e}.home-bottom-nav .nav-icon{display:block;font-size:20px;margin-bottom:3px}
.home-bottom-nav{display:none!important}.global-bottom-nav{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(env(safe-area-inset-bottom,0px) + 10px);z-index:65;width:min(calc(100% - 36px),1080px);display:grid;grid-template-columns:repeat(4,1fr);gap:5px;background:rgba(250,249,246,.96);border:1px solid rgba(222,222,215,.94);border-radius:24px;padding:6px;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);box-shadow:0 10px 30px rgba(50,54,49,.12)}.global-bottom-nav button{appearance:none;border:0;background:transparent;border-radius:18px;min-height:58px;font-size:12px;font-weight:760;color:#7c817c}.global-bottom-nav button.active{background:#edf3eb;color:#2c704e}.global-bottom-nav .nav-icon{display:block;font-size:20px;margin-bottom:3px}main{padding-bottom:76px}.qa-dock{margin-bottom:76px}.food-modal{padding-bottom:calc(env(safe-area-inset-bottom,0px) + 98px)}.modal{z-index:80}.standalone-timer-card{width:min(560px,94vw)}.standalone-timer-card .idle-timer{width:100%;margin:0;border:0;box-shadow:none;padding:0;background:transparent}.standalone-timer-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.standalone-timer-head h2{margin:0;font-size:24px}.standalone-timer-close{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:999px;width:40px;height:40px;font-size:22px}.timer-editor-modal{z-index:90}.utility-page{display:grid;gap:15px;padding:2px 2px 5px}.utility-page-card{border:1px solid var(--line);background:var(--card-solid);border-radius:24px;padding:19px 20px}.utility-page-card h3{margin:0 0 9px;font-size:20px}.utility-page-card ul,.utility-page-card ol{margin:0;padding-left:23px}.utility-page-card li{font-size:17px;line-height:1.52;margin:6px 0}.utility-empty{border:1px dashed var(--line-strong);background:#fafbf7;border-radius:22px;padding:24px;text-align:center;color:var(--muted)}
body.home-active .qa-dock:not(.qa-active){display:none}body.home-active footer{display:none}body.home-active #timerStrip:empty{display:none!important}.qa-dock.qa-active{animation:qaPop .16s ease-out}@keyframes qaPop{from{transform:translateY(8px);opacity:.2}to{transform:none;opacity:1}}

@media(max-width:760px){#app{padding:calc(env(safe-area-inset-top,0px) + 14px) 10px 8px;gap:8px}header{align-items:flex-start}.brand{font-size:21px}.version{display:none}.brand-kicker{font-size:10px}.status{gap:5px;max-width:72%}.status-pill{padding:6px 8px;font-size:10px}.help-btn,.top-food-btn{padding:6px 9px;font-size:11px}.panel{padding:19px;border-radius:24px}.title{font-size:33px}.step-text{font-size:25px}.timer-time{font-size:37px}.toolbar{flex-direction:column}.nav{gap:7px}.nav button{font-size:14px;padding:9px}.timer-controls{grid-template-columns:1fr 1fr}.message{font-size:18px}.qa-dock{gap:8px;padding:8px;min-height:74px;border-radius:19px}.qa-btn{min-width:164px;min-height:58px;font-size:19px;padding:9px 12px}.qa-answer{font-size:14px}.idle-timer-actions,.idle-timer-presets{grid-template-columns:1fr 1fr}.idle-timer-time{font-size:44px}.food-modal{padding:calc(env(safe-area-inset-top,0px) + 14px) 10px calc(env(safe-area-inset-bottom,0px) + 94px)}.global-bottom-nav{width:calc(100% - 20px);bottom:calc(env(safe-area-inset-bottom,0px) + 6px)}main{padding-bottom:72px}.qa-dock{margin-bottom:72px}.food-title{font-size:25px}.food-tabs{gap:5px}.food-tab{min-height:46px;font-size:13px}.food-grid{grid-template-columns:1fr}.food-summary{grid-template-columns:1fr 1fr}.food-channel-grid{grid-template-columns:1fr 1fr 1fr}.food-card{padding:16px;border-radius:21px}.food-form-grid{grid-template-columns:1fr 1fr}}


/* R20: today's menu is the operational center. Pure text, large hierarchy. */
.home-hero{background:linear-gradient(135deg,#f5f7ee 0%,#fbf8f1 100%);min-height:278px}.home-hero:before{display:none}.home-hero-copy{width:100%;padding:30px 32px}.home-hero h2{font-size:46px;max-width:none}.home-today-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:17px}.home-today-list span{display:flex;align-items:center;min-height:52px;padding:10px 14px;border-radius:16px;background:rgba(255,255,252,.84);border:1px solid rgba(218,223,216,.88);font-size:20px;font-weight:800;line-height:1.25}.home-channel-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.home-channel-card{appearance:none;text-align:left;border:1px solid var(--line);background:rgba(255,255,252,.93);border-radius:25px;min-height:132px;padding:22px;box-shadow:var(--shadow-sm)}.home-channel-card strong{display:block;font-size:24px;margin-top:8px}.home-channel-card small{display:block;margin-top:7px;color:var(--muted);font-size:15px;line-height:1.45}.home-channel-icon{font-size:27px}
.today-page,.picker-page,.cook-page{display:grid;gap:15px;padding:2px 2px 5px}.page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:4px 2px}.page-heading h2{font-size:38px;line-height:1;margin:0;letter-spacing:-.035em}.page-heading p{margin:8px 0 0;color:var(--muted);font-size:15px}.page-back{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:999px;padding:9px 14px;font-weight:760}.today-summary{border:1px solid var(--line);background:linear-gradient(135deg,#f1f6ec,#fbf8f1);border-radius:28px;padding:22px}.today-summary strong{font-size:30px;letter-spacing:-.03em}.today-summary-meta{font-size:14px;color:var(--muted);margin-top:6px}.today-list{border:1px solid var(--line);background:var(--card-solid);border-radius:26px;overflow:hidden}.today-row{display:grid;grid-template-columns:46px minmax(0,1fr) auto;align-items:center;gap:13px;padding:19px 20px;border-bottom:1px solid #eceee9}.today-row:last-child{border-bottom:0}.today-number{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:#edf3e9;color:var(--accent);font-weight:850;font-size:17px}.today-name{font-size:22px;font-weight:800}.today-meta{font-size:13px;color:var(--muted);margin-top:4px}.today-start{appearance:none;border:0;background:#2c704e;color:#fff;border-radius:15px;min-height:44px;padding:9px 15px;font-weight:800}.today-add{appearance:none;border:1px dashed #c8d2c9;background:#fafbf7;border-radius:22px;min-height:66px;font-size:17px;font-weight:780}.today-bottom{display:grid;grid-template-columns:1fr 1.6fr;gap:10px}.today-secondary,.today-primary{appearance:none;border-radius:20px;min-height:58px;padding:12px 18px;font-weight:820;font-size:17px}.today-secondary{border:1px solid var(--line);background:var(--card-solid)}.today-primary{border:0;background:#2c704e;color:#fff}.today-finish{appearance:none;width:100%;border:1px solid var(--line);background:var(--card-solid);color:#5f665f;border-radius:18px;min-height:52px;padding:11px 16px;font-size:16px;font-weight:780}.finish-page{display:grid;gap:15px;padding:2px 2px 5px}.finish-summary{border:1px solid var(--line);background:linear-gradient(135deg,#f1f6ec,#fbf8f1);border-radius:26px;padding:22px 23px}.finish-summary-icon{width:44px;height:44px;border-radius:16px;display:grid;place-items:center;background:#e7f0e5;color:#2c704e;font-size:22px;margin-bottom:15px}.finish-summary h3{font-size:28px;line-height:1.15;margin:0;letter-spacing:-.025em}.finish-summary p{font-size:16px;line-height:1.6;color:var(--muted);margin:9px 0 0}.finish-note{border:1px solid var(--line);background:var(--card-solid);border-radius:22px;padding:17px 19px;display:flex;gap:12px;align-items:flex-start}.finish-note-icon{font-size:20px;line-height:1.2}.finish-note strong{display:block;font-size:16px}.finish-note span{display:block;color:var(--muted);font-size:13px;line-height:1.5;margin-top:4px}.private-list{display:grid;gap:10px}.private-row{display:grid;grid-template-columns:30px minmax(0,1fr);align-items:center;gap:12px;border:1px solid var(--line);background:var(--card-solid);border-radius:19px;padding:15px 16px;font-size:18px;font-weight:780;cursor:pointer}.private-row input{appearance:none;-webkit-appearance:none;width:26px;height:26px;border-radius:9px;border:1.5px solid #aab7aa;background:#fff;margin:0;display:grid;place-items:center}.private-row input:checked{background:#2c704e;border-color:#2c704e}.private-row input:checked:after{content:'✓';color:#fff;font-size:17px;font-weight:900;line-height:1}.private-row small{display:block;color:var(--muted);font-size:12px;font-weight:600;margin-top:4px}.finish-modern-actions{position:sticky;bottom:72px;z-index:17;display:grid;grid-template-columns:1fr 1.35fr;gap:10px;padding:10px;border:1px solid rgba(215,221,213,.92);border-radius:21px;background:rgba(250,249,246,.96);box-shadow:0 10px 28px rgba(45,52,47,.10);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}.finish-modern-actions button{appearance:none;min-height:56px;border-radius:17px;padding:12px 16px;font-size:16px;font-weight:820}.finish-modern-secondary{border:1px solid var(--line);background:var(--card-solid)}.finish-modern-primary{border:0;background:#2c704e;color:#fff}.finish-modern-danger{border:0;background:#2c704e;color:#fff}
.picker-sources{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.picker-source{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:22px;padding:16px;text-align:left;min-height:94px}.picker-source.active{border-color:#8caf98;background:#eef5ec}.picker-source strong{display:block;font-size:16px;margin-top:6px}.picker-source small{display:block;color:var(--muted);font-size:12px;margin-top:3px}.picker-search{width:100%;border:1px solid var(--line);background:var(--card-solid);border-radius:18px;min-height:50px;padding:0 16px;font-size:16px}.picker-list{border:1px solid var(--line);background:var(--card-solid);border-radius:24px;overflow:hidden}.picker-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:15px 18px;border-bottom:1px solid #eceee9}.picker-row:last-child{border-bottom:0}.picker-name{font-size:18px;font-weight:790}.picker-meta{font-size:12px;color:var(--muted);margin-top:4px}.picker-add{appearance:none;border:1px solid #9db9a7;background:#f4f8f3;color:#255f45;border-radius:14px;min-height:40px;padding:8px 13px;font-weight:780}.picker-add:disabled{border-color:var(--line);color:var(--muted);background:#f3f4f1}.picker-empty{padding:30px;text-align:center;color:var(--muted)}.inventory-pick-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.inventory-pick{display:grid;grid-template-columns:28px minmax(0,1fr);align-items:center;gap:11px;border:1px solid var(--line);background:var(--card-solid);border-radius:18px;padding:14px 15px;cursor:pointer}.inventory-pick input{appearance:none;-webkit-appearance:none;width:24px;height:24px;border-radius:8px;border:1.5px solid #aab7aa;background:#fff;margin:0;display:grid;place-items:center}.inventory-pick input:checked{background:#2c704e;border-color:#2c704e}.inventory-pick input:checked:after{content:'✓';color:#fff;font-size:16px;font-weight:900;line-height:1}.inventory-pick strong{display:block;font-size:16px}.inventory-pick small{display:block;color:var(--muted);font-size:12px;margin-top:3px}.inventory-pick-actions{position:sticky;bottom:76px;z-index:16;padding:10px;border:1px solid rgba(215,221,213,.92);border-radius:20px;background:rgba(250,249,246,.96);box-shadow:0 10px 28px rgba(45,52,47,.10);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}.inventory-pick-actions button{appearance:none;width:100%;min-height:54px;border:0;border-radius:16px;background:#2c704e;color:#fff;font-size:16px;font-weight:820}.inventory-picked-note{border:1px solid #dce7dc;background:#f3f7f1;border-radius:18px;padding:13px 15px;color:#44604c;font-size:14px}.inventory-back{appearance:none;border:1px solid var(--line);background:var(--card-solid);border-radius:14px;min-height:42px;padding:8px 13px;font-weight:760}.picker-loading{padding:28px;text-align:center;color:var(--muted)}@media(max-width:700px){.inventory-pick-list{grid-template-columns:1fr}}
.cook-top{display:flex;align-items:center;justify-content:space-between;gap:12px}.cook-title-group h2{font-size:34px;margin:0;letter-spacing:-.03em}.cook-dish{font-size:22px;font-weight:820;margin-top:9px}.cook-stage{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.cook-stage span{border-radius:999px;background:#eef0eb;color:#858b84;padding:10px;text-align:center;font-size:14px;font-weight:760}.cook-stage span.active{background:#2c704e;color:#fff}.cook-card{border:1px solid var(--line);background:var(--card-solid);border-radius:26px;padding:23px;box-shadow:var(--shadow-sm)}.cook-step-kicker{font-size:13px;color:var(--accent);font-weight:800}.cook-step-title{font-size:18px;color:var(--muted);margin-top:4px}.cook-step-text{font-size:28px;line-height:1.47;font-weight:700;margin-top:18px;white-space:pre-line}.cook-actions{position:sticky;bottom:72px;z-index:18;display:grid;grid-template-columns:1fr 1.35fr 1fr;gap:9px;margin-top:4px;padding:10px;border:1px solid rgba(215,221,213,.92);border-radius:21px;background:rgba(250,249,246,.96);box-shadow:0 10px 28px rgba(45,52,47,.12);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}.cook-actions button{appearance:none;min-height:56px;border-radius:17px;border:1px solid var(--line);background:var(--card-solid);font-weight:800;font-size:16px}.cook-actions .primary{background:#2c704e;color:#fff;border-color:#2c704e}.cook-standalone-timer{appearance:none;margin-top:14px;border:1px solid #bfd2c7;background:#f7fbf8;color:var(--accent);border-radius:14px;min-height:46px;padding:8px 14px;font-size:14px;font-weight:760}
.prep-page{display:grid;gap:15px;padding:2px 2px 5px}.prep-progress{border:1px solid var(--line);background:linear-gradient(135deg,#f1f6ec,#fbf8f1);border-radius:24px;padding:18px 20px}.prep-progress-top{display:flex;align-items:center;justify-content:space-between;gap:14px}.prep-progress strong{font-size:23px}.prep-progress-count{font-size:16px;font-weight:820;color:var(--accent)}.prep-bar{height:9px;background:#e9ede7;border-radius:999px;overflow:hidden;margin-top:12px}.prep-bar span{display:block;height:100%;background:#2c704e;border-radius:999px}.prep-groups{display:grid;gap:12px}.prep-dish{border:1px solid var(--line);background:var(--card-solid);border-radius:24px;padding:19px 20px}.prep-dish.done{background:#f4f8f2;border-color:#cbd9ca}.prep-dish-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:13px}.prep-dish-name{font-size:21px;font-weight:820}.prep-dish-state{font-size:12px;font-weight:800;color:var(--muted)}.prep-dish.done .prep-dish-state{color:#2c704e}.prep-ref{font-size:12px;color:var(--muted);line-height:1.5;margin:-3px 0 12px}.prep-tasks{display:grid;gap:8px}.prep-check{display:grid;grid-template-columns:28px minmax(0,1fr);gap:10px;align-items:start;padding:11px 12px;border-radius:15px;background:#fafaf7;cursor:pointer}.prep-check input{appearance:none;-webkit-appearance:none;width:24px;height:24px;border-radius:8px;border:1.5px solid #aab7aa;background:#fff;margin:0;display:grid;place-items:center}.prep-check input:checked{background:#2c704e;border-color:#2c704e}.prep-check input:checked:after{content:'✓';color:#fff;font-size:16px;font-weight:900;line-height:1}.prep-check span{font-size:16px;line-height:1.45}.prep-check.checked span{text-decoration:line-through;color:#868d86}.prep-complete{border-radius:22px;background:#edf5eb;padding:18px 20px;color:#255f45;font-weight:780;text-align:center}.prep-actions{display:grid;grid-template-columns:1fr 1.5fr;gap:10px}.prep-actions button{appearance:none;border-radius:19px;min-height:56px;font-weight:820;font-size:16px}.prep-back{border:1px solid var(--line);background:var(--card-solid)}.prep-primary{border:0;background:#2c704e;color:#fff}.prep-primary:disabled{background:#b7c3b9;color:#f7f8f5}

@media(max-width:760px){.modern-header .brand{font-size:38px}.brand-sub{font-size:16px}.header-voice{min-height:54px;padding-right:12px}.header-voice-icon{width:40px;height:40px}.header-voice small{display:none}.home-hero{min-height:270px}.home-hero-copy{width:100%;padding:24px}.home-hero h2{font-size:40px}.home-hero-meta{font-size:16px}.home-today-list{grid-template-columns:1fr 1fr}.home-today-list span{font-size:18px;min-height:50px}.home-channel-card{min-height:124px;padding:19px}.home-channel-card strong{font-size:22px}.home-channel-card small{font-size:14px}.home-food-copy{width:72%;padding:21px}.home-food-card h3{font-size:29px}.home-food-card:after{width:46%}.home-food-actions button{padding:8px 12px;font-size:13px}.home-small-card{padding:18px;min-height:112px}.home-small-title{font-size:22px}.home-small-sub{font-size:14px}.home-priority-title{font-size:20px}.home-priority-list{grid-template-columns:1fr}.home-bottom-nav{border-radius:20px}.more-btn{width:44px;height:44px}}

.food-scan-box{margin-top:14px;border:1px solid #d7e3d9;background:linear-gradient(135deg,#f5faf6,#fbfcf8);border-radius:20px;padding:16px}.food-scan-title{font-size:17px;font-weight:820}.food-scan-copy{font-size:13px;color:var(--muted);line-height:1.5;margin-top:5px}.food-scan-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:13px}.food-scan-button{border:0;background:var(--accent);color:#fff;border-radius:15px;min-height:50px;padding:11px 17px;font-weight:800;font-size:15px}.food-scan-button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}.food-scan-status{margin-top:12px;font-size:13px;color:var(--muted);min-height:20px}.food-scan-status.busy{color:var(--accent);font-weight:720}.food-scan-results{display:grid;gap:10px;margin-top:14px}.food-scan-row{border:1px solid var(--line);background:#fff;border-radius:17px;padding:12px}.food-scan-row-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.food-scan-name{flex:1;min-width:0;border:0;background:transparent;font-size:16px;font-weight:780;outline:none;padding:3px 0}.food-scan-remove{border:0;background:#f4f1eb;border-radius:999px;width:34px;height:34px;font-size:18px}.food-scan-fields{display:grid;grid-template-columns:1.2fr .65fr .65fr;gap:8px;margin-top:9px}.food-scan-fields .food-select,.food-scan-fields .food-input{min-height:42px;font-size:14px;padding:7px 9px}.food-scan-raw{font-size:11px;color:var(--muted);margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.food-scan-commit{margin-top:12px;width:100%}.food-scan-file{display:none}@media(max-width:720px){.food-scan-fields{grid-template-columns:1fr 1fr}.food-scan-fields .food-scan-category{grid-column:1/-1}}


/* R34 · 小K VISUAL REFRESH V2
   Strong visible refresh, page structure and business logic unchanged. */
:root{
  --bg:#f4f6f2;--card:#ffffff;--card-solid:#ffffff;--ink:#173126;--muted:#748078;
  --line:#e4e9e3;--line-strong:#d4ddd5;--accent:#2e7654;--accent-hover:#286949;
  --accent-2:#edf5ef;--soft:#f1f4ef;--shadow:0 10px 30px rgba(30,55,41,.07);--shadow-sm:0 5px 16px rgba(30,55,41,.055)
}
html,body{background:#f4f6f2!important;color:#173126}
body{background-image:linear-gradient(180deg,#fbfcfa 0,#f4f6f2 34%,#eef2ed 100%)!important}
#app{padding:calc(env(safe-area-inset-top,0px) + 22px) 28px 14px!important;gap:16px!important}
main{padding-bottom:92px!important}
.panel{max-width:1180px!important;border-radius:32px!important;padding:30px 34px!important;border-color:#e5eae4!important;background:rgba(255,255,255,.94)!important;box-shadow:0 16px 44px rgba(26,47,35,.065)!important}
.panel.home-mode{background:transparent!important;box-shadow:none!important;border:0!important;padding:0!important}
.content{padding:2px 1px 10px!important}

/* Header */
.modern-header{max-width:1180px!important;min-height:98px!important;padding:0 6px!important}
.modern-header .brand{font-size:58px!important;line-height:.88!important;letter-spacing:-.075em!important;color:#123626!important}
.brand-sub{font-size:19px!important;margin-top:11px!important;color:#718078!important;font-weight:650!important}
.modern-head-actions{gap:12px!important}.header-voice{min-height:68px!important;border-radius:24px!important;padding:9px 21px 9px 10px!important;border-color:#dce5de!important;background:#fff!important;box-shadow:0 8px 24px rgba(29,55,40,.07)!important}.header-voice-icon{width:50px!important;height:50px!important;background:#e4f0e7!important;font-size:23px!important}.header-voice strong{font-size:18px!important;color:#173126}.header-voice small{font-size:12px!important}.more-btn{width:52px!important;height:52px!important;background:#fff!important;border-color:#e0e6e0!important;box-shadow:0 6px 18px rgba(30,55,41,.05)}

/* Home */
.home-dashboard{gap:20px!important;padding:2px 2px 14px!important}.home-hero{min-height:320px!important;border-radius:34px!important;background:linear-gradient(125deg,#eaf4eb 0%,#f4f8f0 56%,#fbf7ef 100%)!important;border:1px solid #dfe8df!important;box-shadow:0 14px 38px rgba(41,73,54,.07)!important}.home-hero:before{display:block!important;content:"";position:absolute;right:-90px;top:-120px;width:330px;height:330px;border-radius:50%;background:rgba(255,255,255,.46)!important;left:auto!important;bottom:auto!important}.home-hero-copy{width:100%!important;padding:40px 42px!important}.home-section-kicker{font-size:16px!important;color:#5a7867!important}.home-hero h2{font-size:58px!important;line-height:1!important;color:#153728!important;margin:14px 0 15px!important}.home-hero-meta{font-size:20px!important;line-height:1.6!important;max-width:850px;color:#68776f!important}.home-today-list{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:12px!important;margin-top:20px!important}.home-today-list span{min-height:60px!important;border-radius:18px!important;background:#fff!important;border:1px solid #e2e9e2!important;font-size:22px!important;padding:13px 17px!important;box-shadow:0 4px 14px rgba(32,56,42,.035)!important}.home-hero-actions{margin-top:28px!important;gap:12px!important}.home-primary,.home-secondary{min-height:60px!important;border-radius:18px!important;padding:13px 26px!important;font-size:18px!important}.home-primary{background:#2e7654!important;box-shadow:0 9px 22px rgba(46,118,84,.18)!important}.home-secondary{background:#fff!important;border-color:#dde5de!important}
.home-channel-grid{gap:14px!important}.home-channel-card{min-height:154px!important;border-radius:26px!important;padding:26px!important;background:#fff!important;border:1px solid #e4e9e4!important;box-shadow:0 8px 24px rgba(33,55,43,.055)!important;position:relative;overflow:hidden}.home-channel-card:after{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:#dbeadf}.home-channel-card:nth-child(1):after{background:#6fa582}.home-channel-card:nth-child(2):after{background:#8db89b}.home-channel-card:nth-child(3):after{background:#d1a960}.home-channel-card:nth-child(4):after{background:#7fa1a0}.home-channel-icon{font-size:31px!important}.home-channel-card strong{font-size:27px!important;line-height:1.1!important;margin-top:13px!important;color:#173426}.home-channel-card small{font-size:16px!important;line-height:1.5!important;margin-top:9px!important;color:#7b867f!important}.home-priority{border-radius:27px!important;background:#fff!important;border:1px solid #e4e9e4!important;padding:23px 25px!important;box-shadow:0 7px 22px rgba(31,52,39,.045)!important}.home-priority-title{font-size:23px!important}.home-priority-note{font-size:14px!important}.home-priority-more{font-size:15px!important}.home-priority-item{min-height:72px!important;border-radius:18px!important;background:#f7f9f6!important;border:1px solid #edf0ec!important;padding:14px 16px!important}.home-priority-name{font-size:18px!important}

/* Page headings */
.today-page,.picker-page,.prep-page,.cook-page,.finish-page,.utility-page{gap:18px!important}.page-heading{padding:7px 4px 12px!important;align-items:flex-start!important}.page-heading h2{font-size:46px!important;line-height:1.02!important;color:#153728!important}.page-heading p{font-size:17px!important;line-height:1.5!important;margin-top:10px!important}.page-back{min-height:48px!important;border-radius:16px!important;background:#fff!important;border-color:#e0e6e0!important}

/* Today menu */
.today-list{background:transparent!important;border:0!important;border-radius:0!important;overflow:visible!important;display:grid!important;gap:11px!important}.today-row{grid-template-columns:52px minmax(0,1fr) auto!important;padding:20px 22px!important;border:1px solid #e4e9e4!important;border-radius:22px!important;background:#fff!important;box-shadow:0 6px 18px rgba(30,53,40,.045)!important}.today-row:last-child{border-bottom:1px solid #e4e9e4!important}.today-number{width:40px!important;height:40px!important;background:#e7f1e8!important;font-size:18px!important}.today-name{font-size:24px!important;color:#173426}.today-meta{font-size:14px!important;margin-top:6px!important}.today-start{min-height:48px!important;border-radius:15px!important;font-size:15px!important;background:#2e7654!important}.today-add{min-height:72px!important;border-radius:20px!important;background:#f7faf7!important;border-color:#cddbcf!important;font-size:18px!important}.today-bottom{gap:12px!important}.today-secondary,.today-primary{min-height:64px!important;border-radius:19px!important;font-size:18px!important}.today-primary{background:#2e7654!important}.today-finish{min-height:56px!important;border-radius:18px!important;font-size:16px!important;background:#fff!important}

/* Picker */
.picker-sources{gap:14px!important}.picker-source{min-height:124px!important;border-radius:24px!important;padding:22px!important;background:#fff!important;border-color:#e3e8e3!important;box-shadow:0 5px 18px rgba(29,54,40,.04)!important}.picker-source.active{background:#eaf4ec!important;border-color:#a9c8b2!important;box-shadow:0 0 0 3px rgba(46,118,84,.055)!important}.picker-source>span{font-size:26px!important}.picker-source strong{font-size:20px!important;margin-top:10px!important}.picker-source small{font-size:14px!important;line-height:1.5!important}.picker-search{min-height:58px!important;border-radius:17px!important;font-size:17px!important;background:#fff!important}.picker-list{background:transparent!important;border:0!important;border-radius:0!important;display:grid!important;gap:9px!important;overflow:visible!important}.picker-row{border:1px solid #e5e9e4!important;border-radius:18px!important;background:#fff!important;padding:17px 19px!important;box-shadow:0 4px 14px rgba(29,50,38,.035)!important}.picker-row:last-child{border-bottom:1px solid #e5e9e4!important}.picker-name{font-size:20px!important}.picker-meta{font-size:13px!important;line-height:1.45!important}.picker-add{min-height:44px!important;border-radius:14px!important;background:#edf6ef!important;border-color:#b8d0bf!important;color:#286b4b!important}.inventory-picked-note{padding:16px 18px!important;border-radius:18px!important}.inventory-pick-list{gap:12px!important}.inventory-pick{padding:17px!important;border-radius:19px!important;background:#fff!important;box-shadow:0 4px 14px rgba(31,52,40,.035)!important}.inventory-pick strong{font-size:18px!important}.inventory-pick small{font-size:13px!important}.inventory-pick input{width:26px!important;height:26px!important}.inventory-pick-actions{bottom:96px!important;border-radius:20px!important;background:rgba(255,255,255,.98)!important}

/* Food management */
.food-modal{background:#f4f6f2!important;padding-top:calc(env(safe-area-inset-top,0px) + 24px)!important}.food-shell{max-width:1180px!important}.food-head{margin-bottom:20px!important}.food-title{font-size:46px!important;color:#153728!important}.food-sub{font-size:16px!important}.food-close{min-height:48px!important;border-radius:16px!important}.food-tabs{padding:6px!important;border-radius:18px!important;background:#eaf0ea!important}.food-tab{min-height:52px!important;font-size:15px!important}.food-tab.active{background:#fff!important;color:#286b4b!important;box-shadow:0 3px 10px rgba(28,50,37,.04)!important}.food-grid{gap:18px!important}.food-card{border-radius:25px!important;padding:24px!important;border-color:#e4e9e4!important;background:#fff!important;box-shadow:0 7px 21px rgba(31,54,41,.04)!important}.food-card h3{font-size:23px!important}.food-card-note{font-size:15px!important}.food-stat{padding:16px!important;border-radius:17px!important}.food-stat strong{font-size:25px!important}.food-row{padding:15px 16px!important;border-radius:17px!important}.food-row-name{font-size:17px!important}.food-input,.food-select{min-height:48px!important;font-size:16px!important}.food-primary{min-height:54px!important;background:#2e7654!important}.food-channel{min-height:66px!important;border-radius:17px!important}.food-scan-box{padding:20px!important;border-radius:20px!important}

/* Prep */
.prep-progress{padding:22px 24px!important;border-radius:24px!important;background:#edf6ef!important;border-color:#dbe8dd!important}.prep-progress strong{font-size:26px!important}.prep-progress-count{font-size:18px!important}.prep-bar{height:11px!important;margin-top:15px!important}.prep-dish{padding:22px 23px!important;border-radius:24px!important;background:#fff!important;border-color:#e4e9e4!important;box-shadow:0 6px 18px rgba(31,52,40,.04)!important}.prep-dish-name{font-size:24px!important}.prep-dish-state{font-size:13px!important}.prep-ref{font-size:14px!important;line-height:1.55!important}.prep-tasks{gap:10px!important}.prep-check{grid-template-columns:31px minmax(0,1fr)!important;padding:14px 15px!important;border-radius:16px!important;background:#f7f9f6!important;border:1px solid #ecf0eb!important}.prep-check input{width:27px!important;height:27px!important}.prep-check span{font-size:18px!important;line-height:1.5!important}.prep-complete{font-size:17px!important;padding:20px!important}

/* Cooking */
.cook-title-group h2{font-size:46px!important;color:#153728!important}.cook-dish{font-size:26px!important}.cook-stage{gap:10px!important}.cook-stage span{padding:12px!important;font-size:15px!important}.cook-stage span.active{background:#2e7654!important}.cook-card{padding:30px!important;border-radius:26px!important;background:#fff!important;border-color:#e4e9e4!important;box-shadow:0 8px 24px rgba(31,52,40,.045)!important}.cook-step-kicker{font-size:15px!important}.cook-step-title{font-size:20px!important}.cook-step-text{font-size:34px!important;line-height:1.56!important;margin-top:22px!important;color:#1b3024!important}.tips h3{font-size:16px!important}.tips li{font-size:17px!important}.step-timer,.cook-standalone-timer{border-radius:18px!important;background:#f1f7f2!important;border-color:#d9e6dc!important}.timer-time{font-size:48px!important}.cook-actions{bottom:96px!important;border-radius:22px!important;background:rgba(255,255,255,.98)!important;border-color:#e2e7e2!important;box-shadow:0 12px 30px rgba(29,49,37,.10)!important}.cook-actions button{min-height:60px!important;border-radius:17px!important;font-size:17px!important}.cook-actions .primary{background:#2e7654!important}

/* Finish + shopping/timeline */
.finish-summary{padding:25px!important;border-radius:25px!important;background:#edf6ef!important}.finish-summary h3{font-size:33px!important}.finish-summary p{font-size:17px!important}.finish-note,.private-row,.utility-page-card{border-radius:22px!important;background:#fff!important;border-color:#e4e9e4!important;box-shadow:0 5px 17px rgba(31,52,40,.04)!important}.private-row{padding:18px!important;font-size:19px!important}.utility-page-card{padding:24px!important}.utility-page-card h3{font-size:24px!important}.utility-page-card li{font-size:18px!important;line-height:1.55!important}.finish-modern-actions{bottom:96px!important;background:rgba(255,255,255,.98)!important}

/* Global nav */
.global-bottom-nav{max-width:760px!important;width:calc(100% - 44px)!important;bottom:calc(env(safe-area-inset-bottom,0px) + 12px)!important;border-radius:24px!important;padding:7px!important;background:rgba(255,255,255,.97)!important;border:1px solid #dfe6df!important;box-shadow:0 14px 40px rgba(25,48,34,.13)!important;backdrop-filter:blur(20px)!important;-webkit-backdrop-filter:blur(20px)!important}.global-bottom-nav button{min-height:58px!important;border-radius:18px!important;font-size:13px!important;color:#748078!important;gap:4px!important}.global-bottom-nav .nav-icon{font-size:20px!important}.global-bottom-nav button.active{background:#e8f3ea!important;color:#286b4b!important;font-weight:850!important}

/* Timer / modal */
.modal{background:rgba(18,34,25,.31)!important;backdrop-filter:blur(14px)!important;-webkit-backdrop-filter:blur(14px)!important}.modal-card{border-radius:30px!important;padding:28px!important;background:#fff!important;border-color:#e2e7e2!important;box-shadow:0 30px 88px rgba(20,39,29,.22)!important}.modal-card h2{font-size:28px!important;color:#153728!important}.standalone-timer-card{width:min(520px,94vw)!important}.idle-timer{border:0!important;background:transparent!important;box-shadow:none!important;padding:10px 0 0!important}.idle-timer-title{font-size:20px!important}.idle-timer-time{font-size:68px!important;color:#173426!important;margin:26px 0!important}.idle-timer-actions button,.idle-timer-presets button{min-height:54px!important;border-radius:16px!important;background:#f5f8f5!important;border-color:#e2e7e2!important;font-size:16px!important}.idle-timer-actions button.primary{background:#2e7654!important}.time-inputs input{background:#f5f8f5!important}.modal-actions button{min-height:54px!important;border-radius:16px!important}.modal-actions .primary{background:#2e7654!important}

/* Q&A */
.qa-dock{max-width:1180px!important;border-radius:24px!important;background:rgba(255,255,255,.97)!important;border-color:#e3e8e3!important;box-shadow:0 9px 28px rgba(30,52,40,.06)!important}.qa-btn{background:#2e7654!important;border-radius:17px!important}.qa-status{color:#2e7654!important}
footer{opacity:.42!important}

@media(max-width:760px){
  #app{padding:calc(env(safe-area-inset-top,0px) + 18px) 16px 10px!important;gap:13px!important}
  .modern-header{min-height:88px!important}.modern-header .brand{font-size:49px!important}.brand-sub{font-size:17px!important}.header-voice{min-height:58px!important}.header-voice-icon{width:43px!important;height:43px!important}.header-voice small{display:none!important}.more-btn{width:46px!important;height:46px!important}
  .home-hero{min-height:305px!important}.home-hero-copy{padding:31px 28px!important}.home-hero h2{font-size:49px!important}.home-hero-meta{font-size:18px!important}.home-today-list span{font-size:20px!important;min-height:56px!important}.home-channel-card{min-height:145px!important;padding:22px!important}.home-channel-card strong{font-size:25px!important}.home-channel-card small{font-size:15px!important}
  .page-heading h2{font-size:40px!important}.page-heading p{font-size:16px!important}.today-name{font-size:22px!important}.today-row{padding:18px!important}.food-title{font-size:39px!important}.cook-title-group h2{font-size:39px!important}.cook-step-text{font-size:30px!important}.prep-check span{font-size:17px!important}
  .global-bottom-nav{width:calc(100% - 24px)!important;bottom:calc(env(safe-area-inset-bottom,0px) + 8px)!important}.global-bottom-nav button{min-height:56px!important}
}


/* R35 · Approved mockup UI: structural visual parity with approved review board */
:root{--mock-green:#2f8b5b;--mock-green-dark:#17613d;--mock-green-soft:#edf7f0;--mock-ink:#10261a;--mock-muted:#6e7971;--mock-line:#e6ebe7;--mock-bg:#f7faf8}
html,body{background:var(--mock-bg)!important;color:var(--mock-ink)!important}
#app{padding:calc(env(safe-area-inset-top,0px) + 10px) 18px 8px!important;gap:8px!important}
.modern-header{max-width:980px!important;min-height:72px!important;padding:0 4px!important}.modern-header .brand{font-size:42px!important;color:#123b26!important}.brand-sub{font-size:15px!important;color:#6c766f!important;margin-top:4px!important}.header-voice{min-height:48px!important;border:0!important;background:#fff4ea!important;box-shadow:none!important;padding:6px 14px 6px 7px!important}.header-voice-icon{width:36px!important;height:36px!important;background:#f4e1cf!important}.header-voice small{display:none!important}.more-btn{width:42px!important;height:42px!important;border:0!important;box-shadow:none!important;background:#fff!important}.modern-head-actions>.dot{display:none!important}
body:not([data-k-page="home"]) .modern-header{display:none!important}body:not([data-k-page="home"]) #app{padding-top:calc(env(safe-area-inset-top,0px) + 20px)!important}
main{padding-bottom:80px!important}.panel.home-mode>.content{overflow:auto!important}.home-dashboard,.mock-page{width:min(100%,980px)!important;margin:0 auto!important;padding:0 2px 16px!important;display:grid!important;gap:14px!important}
.mock-welcome{min-height:238px;border-radius:26px;background:linear-gradient(135deg,#fffdf8 0,#f5f0e6 100%);border:1px solid #ece9e1;padding:30px 32px;display:grid;grid-template-columns:minmax(0,1fr) 220px;align-items:center;box-shadow:0 10px 30px rgba(34,55,43,.05)}.mock-greeting{font-size:38px;font-weight:900;letter-spacing:-.04em}.mock-greeting-sub{font-size:19px;color:#687269;margin-top:8px}.mock-big-primary{border:0;background:linear-gradient(135deg,#31975f,#25794e);color:#fff;border-radius:16px;min-height:62px;padding:14px 26px;font-size:20px;font-weight:850;margin-top:28px;box-shadow:0 10px 22px rgba(47,139,91,.18)}.mock-primary-note{font-size:13px;color:#7d867f;margin:8px 6px 0}.mock-welcome-deco{justify-self:end;text-align:right;color:#6b746d;font-family:"Kaiti SC","STKaiti",serif;font-size:18px;line-height:1.5;transform:rotate(-4deg);padding-right:16px}.mock-welcome-deco span{display:block}.mock-welcome-deco b{display:block;font-family:sans-serif;font-size:31px;margin-top:9px;color:#8c958e}
.mock-home-today,.mock-tip,.mock-menu-list,.mock-shopping-card,.mock-prep-progress,.mock-prep-card,.mock-cook-card,.mock-source-card,.mock-recipe-section{background:#fff;border:1px solid var(--mock-line);border-radius:22px;box-shadow:0 6px 22px rgba(30,54,40,.045)}.mock-home-today{padding:20px}.mock-section-head{display:flex;justify-content:space-between;align-items:center}.mock-section-head strong{font-size:24px}.mock-section-head small{font-size:14px;color:var(--mock-muted);margin-left:10px}.mock-text-link,.mock-text-action{border:0;background:transparent;color:var(--mock-green-dark);font-weight:760;min-height:42px}.mock-home-dishes{display:grid;gap:6px;margin-top:12px}.mock-home-dish{border:0;background:#fafcfb;border-radius:15px;display:grid;grid-template-columns:34px 1fr auto;align-items:center;text-align:left;min-height:56px;padding:8px 13px}.mock-home-dish-num{width:27px;height:27px;border-radius:50%;background:#e8f5ec;color:#2b754d;display:flex;align-items:center;justify-content:center;font-weight:850}.mock-home-dish strong{font-size:17px}.mock-home-dish-arrow{font-size:24px;color:#9aa49d}
.mock-feature-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.mock-feature-card{border:1px solid var(--mock-line);background:#fff;border-radius:20px;min-height:138px;padding:20px 15px;text-align:center;box-shadow:0 5px 18px rgba(28,52,38,.04)}.mock-feature-icon{width:48px;height:48px;margin:0 auto 12px;border-radius:15px;background:#edf7f0;color:#2f8b5b;display:flex;align-items:center;justify-content:center;font-size:25px;font-weight:900}.mock-feature-card strong{display:block;font-size:18px}.mock-feature-card small{display:block;font-size:13px;line-height:1.4;color:var(--mock-muted);margin-top:7px}.mock-tip{padding:18px 22px;display:flex;align-items:center;gap:14px;background:linear-gradient(90deg,#f6faf6,#fff)}.mock-tip-icon{font-size:28px}.mock-tip strong{font-size:17px;display:block}.mock-tip small{font-size:14px;color:var(--mock-muted);display:block;margin-top:3px}
.mock-page-head{padding:2px 5px 10px}.mock-page-head h2{font-size:41px;line-height:1.05;margin:0;color:#123924;letter-spacing:-.045em}.mock-page-head p{font-size:16px;color:var(--mock-muted);margin:9px 0 0}.mock-menu-list{padding:8px;display:grid;gap:5px}.mock-menu-row{border:0;background:#fff;border-radius:16px;min-height:82px;padding:12px 14px;display:grid;grid-template-columns:44px minmax(0,1fr) 30px;gap:11px;align-items:center;text-align:left}.mock-menu-row:not(:last-child){border-bottom:1px solid #eef1ee}.mock-menu-index{width:36px;height:36px;border-radius:50%;background:#e9f4eb;color:#2b754d;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:850}.mock-menu-copy strong{font-size:20px;display:block}.mock-menu-copy small{font-size:13px;color:var(--mock-muted);display:block;margin-top:6px}.mock-menu-arrow{font-size:27px;color:#9ba49e}.mock-soft-action{min-height:58px;border:0;border-radius:17px;background:#eaf4ed;color:#256d48;font-size:17px;font-weight:800}.mock-bottom-primary{min-height:66px;border:0;border-radius:18px;background:linear-gradient(135deg,#32925d,#28784e);color:#fff;font-size:19px;font-weight:850;box-shadow:0 10px 24px rgba(46,134,86,.15)}.mock-inline-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px}.mock-inline-actions button{min-height:48px;border:1px solid var(--mock-line);background:#fff;border-radius:15px;font-weight:730}.mock-empty{padding:35px;text-align:center;background:#fff;border:1px dashed #dfe6e0;border-radius:22px}.mock-empty strong,.mock-empty span{display:block}.mock-empty strong{font-size:22px}.mock-empty span{color:var(--mock-muted);margin-top:8px}
.mock-source-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.mock-source-card{min-height:180px;padding:24px;display:grid;grid-template-columns:58px 1fr 34px;align-items:center;text-align:left}.mock-source-card.active{border-color:#a8cfb5;background:linear-gradient(135deg,#f3faf5,#fff)}.mock-source-icon{width:54px;height:54px;border-radius:16px;background:#e8f5ec;color:#2e8155;display:flex;align-items:center;justify-content:center;font-size:28px}.mock-source-copy strong{font-size:24px;display:block}.mock-source-copy small{font-size:14px;color:var(--mock-muted);display:block;margin-top:8px;line-height:1.45}.mock-source-arrow{font-size:30px;color:#2e8155}.mock-picker-body{display:grid;gap:13px}.mock-recipe-section{padding:14px}.mock-search{width:100%;min-height:52px;border:0;background:#f4f6f4;border-radius:15px;padding:11px 16px;font-size:16px;outline:none}.mock-recipe-list{display:grid;gap:2px;margin-top:9px}.mock-recipe-row{display:flex;align-items:center;gap:15px;padding:13px 8px;border-bottom:1px solid #edf1ee}.mock-recipe-copy{flex:1;min-width:0}.mock-recipe-copy strong{font-size:18px}.mock-recipe-copy small{display:block;color:var(--mock-muted);font-size:13px;margin-top:5px}.mock-add-recipe{border:0;background:#e9f6ed;color:#247249;border-radius:999px;min-height:38px;padding:7px 14px;font-weight:800}.mock-add-recipe:disabled{background:#f1f3f1;color:#9ca29e}.mock-section-title strong{font-size:23px;display:block}.mock-section-title small{font-size:14px;color:var(--mock-muted);display:block;margin-top:5px}.mock-inventory-list{background:#fff;border:1px solid var(--mock-line);border-radius:20px;overflow:hidden}.mock-inventory-row{display:grid;grid-template-columns:30px 1fr auto;align-items:center;gap:12px;min-height:64px;padding:10px 16px;border-bottom:1px solid #edf1ee}.mock-inventory-row:last-child{border-bottom:0}.mock-inventory-row input{width:22px;height:22px;accent-color:var(--mock-green)}.mock-inventory-name strong{font-size:18px;display:block}.mock-inventory-name small{font-size:12px;color:var(--mock-muted);display:block;margin-top:4px}.mock-category-pill{border-radius:999px;background:#eaf5ed;color:#327b52;padding:6px 10px;font-size:12px;font-weight:760}.mock-selected-note{background:#edf7f0;border-radius:15px;padding:13px 16px;color:#356048;font-size:14px}
.mock-prep-progress{padding:20px 22px}.mock-prep-progress>div:first-child{display:flex;justify-content:space-between;align-items:center}.mock-prep-progress strong{font-size:22px}.mock-prep-progress span{font-size:17px;color:#2c7950;font-weight:800}.mock-progress-bar{height:9px;background:#edf1ed;border-radius:99px;overflow:hidden;margin-top:15px}.mock-progress-bar i{display:block;height:100%;background:#369463;border-radius:99px}.mock-prep-groups{display:grid;gap:12px}.mock-prep-card{padding:20px}.mock-prep-head{display:flex;justify-content:space-between;align-items:center}.mock-prep-head strong{font-size:22px}.mock-prep-head span{font-size:13px;color:#2b7a50;background:#eaf6ee;border-radius:999px;padding:6px 10px}.mock-prep-ref{font-size:13px;color:var(--mock-muted);white-space:pre-line;margin-top:8px}.mock-prep-tasks{display:grid;gap:7px;margin-top:13px}.mock-check-row{display:grid;grid-template-columns:28px 1fr;gap:11px;align-items:flex-start;background:#f8faf8;border-radius:14px;padding:12px}.mock-check-row input{width:23px;height:23px;accent-color:var(--mock-green)}.mock-check-row span{font-size:16px;line-height:1.45}.mock-check-row.checked span{text-decoration:line-through;color:#8a928c}.mock-success{padding:17px;border-radius:17px;background:#eaf6ee;color:#266e47;font-weight:800;text-align:center}
.mock-cook-head h2{font-size:38px;margin:0;color:#123924}.mock-cook-head p{color:var(--mock-muted);margin:7px 0 0}.mock-stage{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.mock-stage span{background:#eff2ef;border-radius:999px;padding:10px;text-align:center;color:#7b847e;font-size:14px;font-weight:750}.mock-stage span.active{background:#2e8959;color:#fff}.mock-cook-card{padding:28px}.mock-step-kicker{font-size:14px;color:#2e7c51;font-weight:850}.mock-step-text{font-size:29px;line-height:1.55;font-weight:700;margin-top:15px;white-space:pre-line}.mock-k-tip{margin-top:18px;background:#eff7f1;border-radius:15px;padding:14px;color:#496653}.mock-k-tip strong{display:block;color:#2c7950;margin-bottom:5px}.mock-k-tip span{font-size:14px;line-height:1.45}.mock-cook-actions{position:sticky;bottom:82px;display:grid;grid-template-columns:1fr 1fr;gap:10px;background:rgba(247,250,248,.96);padding:8px;border-radius:19px;z-index:5}.mock-cook-actions button{min-height:57px;border:1px solid var(--mock-line);background:#fff;border-radius:15px;font-size:17px;font-weight:800}.mock-cook-actions .primary{background:#2e8959;color:#fff;border-color:#2e8959}.mock-cook-timer-button{appearance:none;width:100%;margin-top:18px;min-height:62px;border:1px solid #d7e5db;background:#f3f8f4;color:#246e49;border-radius:17px;font-size:18px;font-weight:820}.mock-cook-timer-button:active{transform:scale(.99)}.step-timer-manual{padding:17px 18px!important}.timer-controls-single{grid-template-columns:1fr!important}.timer-controls button{min-height:60px!important;font-size:16px!important;padding:11px 12px!important}.timer-controls .timer-touch-main{min-height:66px!important;font-size:18px!important}.step-timer-manual .timer-touch-main{width:100%;font-size:19px!important}.timer-time{cursor:pointer;padding:5px 0}.modal-actions button{min-height:60px!important;font-size:18px!important}.time-inputs input{min-height:66px}.kitchen-timer-main{min-height:66px!important;font-size:20px!important}.kitchen-timer-presets button,.kitchen-timer-secondary button{min-height:58px!important;font-size:16px!important}
.mock-shopping-card{padding:20px}.mock-shopping-card>strong{font-size:22px}.mock-shopping-list{display:grid;margin-top:10px}.mock-shopping-row{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:11px;min-height:55px;padding:8px;border-bottom:1px solid #edf1ee}.mock-shopping-row input{width:22px;height:22px;accent-color:var(--mock-green)}.shopping-item-copy{min-width:0;display:block}.shopping-item-text{display:block;font-size:17px;white-space:normal;overflow-wrap:anywhere}.shopping-stock-badge{display:inline-flex!important;align-items:center;justify-content:center;min-height:31px;padding:5px 10px;border-radius:999px;background:#eaf4ed;color:#2d7650;border:1px solid #cfe2d5;font-size:13px!important;font-weight:820;white-space:nowrap}.mock-shopping-row.checked .shopping-stock-badge{opacity:.58}.shopping-modal-body .shopping-stock-badge{font-size:12px!important;padding:4px 9px;min-height:29px}
.shopping-modal-card{width:min(760px,94vw);max-height:min(82vh,720px);display:flex;flex-direction:column;padding:0;overflow:hidden}.shopping-modal-head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:21px 24px 15px;border-bottom:1px solid #e7ece8}.shopping-modal-head h2{margin:0;font-size:27px}.shopping-modal-head p{margin:5px 0 0;color:var(--mock-muted);font-size:13px}.shopping-modal-close{appearance:none;border:0;background:#eef2ef;border-radius:50%;width:42px;height:42px;font-size:25px;line-height:1}.shopping-modal-body{padding:14px 18px 20px;overflow:auto;display:grid;gap:10px}.shopping-modal-body .mock-shopping-card{padding:15px 17px;border-radius:18px;box-shadow:none}.shopping-modal-body .mock-shopping-card>strong{font-size:18px}.shopping-modal-body .mock-shopping-row{min-height:49px}.shopping-modal-empty{padding:36px 18px;text-align:center;color:#6f7b73;font-size:16px}
/* Food screen mirrors approved list-style review */
.food-modal{background:var(--mock-bg)!important}.food-shell{max-width:980px!important}.food-title{font-size:40px!important;color:#123924!important}.food-sub{font-size:15px!important}.food-tabs{background:transparent!important;border:0!important;padding:0!important;display:flex!important;gap:7px!important;overflow:auto!important}.food-tab{min-height:40px!important;padding:8px 14px!important;border-radius:999px!important;background:#eef2ef!important;white-space:nowrap!important}.food-tab.active{background:#2f8b5b!important;color:#fff!important;box-shadow:none!important}.food-grid{grid-template-columns:1fr 1fr!important}.food-card{border-radius:20px!important;box-shadow:0 6px 22px rgba(30,54,40,.04)!important}.food-row{background:#fff!important;border-bottom:1px solid #edf1ee!important;border-radius:0!important;padding:12px 5px!important}.food-row:last-child{border-bottom:0!important}.food-list{gap:0!important}.food-stat{background:#f4f7f4!important}
/* Exact-like global bottom bar from review board */
.global-bottom-nav{width:100%!important;max-width:none!important;left:0!important;transform:none!important;bottom:0!important;border-radius:0!important;border:0!important;border-top:1px solid #e4e9e5!important;background:rgba(255,255,255,.97)!important;box-shadow:0 -4px 18px rgba(29,50,38,.04)!important;padding:5px max(18px,calc((100% - 900px)/2)) calc(env(safe-area-inset-bottom,0px) + 5px)!important}.global-bottom-nav button{min-height:57px!important;border-radius:12px!important;font-size:12px!important}.global-bottom-nav button.active{background:transparent!important;color:#23804f!important}.global-bottom-nav .nav-icon{font-size:19px!important}
/* Review-board timer modal */
.modal{background:rgba(22,31,26,.38)!important}.standalone-timer-card{width:min(470px,92vw)!important;border-radius:25px!important;padding:25px!important}.standalone-timer-head{justify-content:center!important;position:relative!important}.standalone-timer-head h2{font-size:25px!important}.standalone-timer-close{position:absolute!important;right:0!important;top:-3px!important;border:0!important;background:transparent!important}.idle-timer-time{font-size:62px!important}.idle-timer-presets button,.idle-timer-actions button{border:0!important;background:#f2f5f2!important}.idle-timer-presets button:first-child{background:#2f8b5b!important;color:#fff!important}.idle-timer-actions button.primary{background:#2f8b5b!important;color:#fff!important}
@media(max-width:760px){.mock-welcome{grid-template-columns:1fr!important;min-height:220px!important;padding:24px!important}.mock-welcome-deco{display:none!important}.mock-feature-grid{grid-template-columns:repeat(2,1fr)!important}.mock-source-grid{grid-template-columns:1fr!important}.mock-page-head h2{font-size:34px!important}.mock-greeting{font-size:32px!important}.mock-step-text{font-size:25px!important}.food-grid{grid-template-columns:1fr!important}}

/* R49.2 · landscape one-screen cooking cockpit */
body[data-k-page="dashboard"] #timerStrip{display:none!important}
body[data-k-page="dashboard"] #app{padding-top:calc(env(safe-area-inset-top,0px) + 8px)!important;gap:5px!important}
body[data-k-page="dashboard"] header{min-height:38px!important}
body[data-k-page="dashboard"] .panel{padding:10px 12px 8px!important;border-radius:22px!important;overflow:hidden!important}
body[data-k-page="dashboard"] .content{overflow:hidden!important;padding:0!important}
.r49-dashboard{height:100%;min-height:0;display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:9px;overflow:hidden;padding-bottom:2px}
.r49-dash-head{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:0 3px}.r49-dash-head h2{margin:0;font-size:30px;letter-spacing:-.035em;color:#153728}.r49-dash-head p{margin:3px 0 0;font-size:12px;color:var(--muted)}.r49-dash-status{display:inline-flex;align-items:center;gap:7px;border-radius:999px;background:#edf7f0;color:#28734d;padding:8px 12px;font-size:12px;font-weight:820;white-space:nowrap}.r49-dash-status:before{content:'';width:8px;height:8px;border-radius:50%;background:#27a265}.r50-dash-actions{display:flex;align-items:center;gap:9px}.r50-finish-day{appearance:none;border:1px solid #dfb9b2;background:#fff8f6;color:#94483f;border-radius:15px;min-height:42px;padding:8px 14px;font-size:13px;font-weight:880;white-space:nowrap;box-shadow:0 4px 12px rgba(148,72,63,.06)}.r50-finish-day:active{transform:scale(.98)}
.r49-dash-body{min-height:0;display:grid;grid-template-columns:minmax(0,1.32fr) minmax(300px,.88fr);gap:10px;overflow:hidden}.r49-card{border:1px solid #e1e7e1;background:#fff;border-radius:20px;box-shadow:0 5px 18px rgba(31,55,40,.045)}
.r49-current{min-height:0;padding:18px 20px;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;gap:9px;overflow:hidden}.r49-label{display:flex;align-items:center;gap:8px;font-size:13px;color:#397554;font-weight:850}.r49-current-title{display:flex;align-items:flex-end;justify-content:space-between;gap:14px}.r49-current-title h3{font-size:30px;line-height:1;margin:0;color:#163b29;letter-spacing:-.03em}.r49-step-count{font-size:14px;color:#657269;font-weight:760;white-space:nowrap}.r49-progress{height:7px;border-radius:99px;background:#edf1ed;overflow:hidden;margin-top:7px}.r49-progress i{display:block;height:100%;border-radius:99px;background:#2f8b5b}.r49-step-copy{align-self:center;font-size:25px;line-height:1.42;font-weight:720;color:#1e3126;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.r49-current-bottom{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,.76fr);gap:10px;align-items:stretch}.r49-main-timer{border-radius:16px;background:#f3f8f4;border:1px solid #dbe8de;padding:11px 13px;min-height:78px;display:flex;align-items:center;justify-content:space-between;gap:10px}.r49-main-timer-copy small{display:block;color:#6f7c73;font-size:11px;font-weight:720}.r49-main-timer-copy strong{display:block;font-size:29px;line-height:1.05;margin-top:3px;font-variant-numeric:tabular-nums;color:#183e2b}.r49-main-timer-copy strong.done{color:#a24c43}.r49-main-timer button,.r49-continue{appearance:none;border:0;border-radius:14px;min-height:54px;padding:10px 15px;font-size:15px;font-weight:850}.r49-main-timer button{background:#e5f1e8;color:#246e49;min-width:92px}.r49-continue{width:100%;background:#2f8b5b;color:#fff}
.r49-side{min-height:0;display:grid;grid-template-rows:1.2fr 1fr .72fr;gap:9px;overflow:hidden}.r49-side-card{padding:13px 14px;min-height:0;overflow:hidden}.r49-side-card h4{margin:0;font-size:14px;color:#456653}.r49-other-list{display:grid;gap:5px;margin-top:7px}.r49-other-row{appearance:none;width:100%;border:0;background:#f6f9f6;border-radius:11px;min-height:38px;padding:7px 9px;display:flex;align-items:center;justify-content:space-between;gap:8px;text-align:left;color:#183b29}.r49-other-row strong{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.r49-other-row span{flex:0 0 auto;font-size:10px;font-weight:820;color:#6e7b73;background:#fff;border:1px solid #e1e8e2;border-radius:999px;padding:4px 7px}.r49-other-row.started span{color:#2c7951;background:#edf7f0;border-color:#dceade}.r49-other-empty{font-size:12px;color:#6f7d73;padding-top:10px}.r49-timer-mini-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:8px}.r49-timer-mini{appearance:none;text-align:left;border:0;background:#f5f8f5;border-radius:13px;padding:8px 9px;min-height:63px;overflow:hidden}.r49-timer-mini .dish{display:block;font-size:10px;color:#68776e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.r49-timer-mini strong{display:block;font-size:19px;line-height:1;margin-top:5px;font-variant-numeric:tabular-nums;color:#173d2a}.r49-timer-mini.finished strong{color:#a14b43}.r49-timer-center-link{appearance:none;border:0;background:transparent;color:#2b7b51;font-weight:820;font-size:12px;padding:6px 0 0}.r49-prep-entry{height:calc(100% - 21px);display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center}.r49-prep-copy{min-width:0}.r49-prep-copy strong{display:block;font-size:16px;color:#173b29}.r49-prep-copy small{display:block;margin-top:4px;font-size:11px;color:#6f7d73}.r49-prep-progress{height:6px;border-radius:99px;background:#edf1ed;overflow:hidden;margin-top:7px}.r49-prep-progress i{display:block;height:100%;border-radius:99px;background:#2f8b5b}.r49-prep-button{appearance:none;border:0;background:#2f8b5b;color:#fff;border-radius:13px;min-height:48px;min-width:118px;padding:9px 13px;font-size:14px;font-weight:850;box-shadow:0 6px 16px rgba(47,139,91,.16)}.r49-pending-empty{font-size:12px;color:#6f7d73;padding-top:9px}
.r49-quickbar{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.r49-quickbar button{appearance:none;border:1px solid #dce6df;background:#fff;border-radius:17px;min-height:62px;padding:9px 14px;display:flex;align-items:center;justify-content:center;gap:10px;font-size:16px;font-weight:850;color:#214332;box-shadow:0 5px 14px rgba(31,68,47,.04)}.r49-quickbar .ico{width:32px;height:32px;border-radius:10px;background:#eaf4ed;color:#2b7d52;display:grid;place-items:center;font-size:17px}
.timer-center-modal{z-index:92!important}.timer-center-card{width:min(1040px,96vw)!important;height:min(650px,88vh);padding:18px 20px!important;border-radius:25px!important;display:grid;grid-template-rows:auto minmax(0,1fr);overflow:hidden}.timer-center-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;position:relative}.timer-center-head h2{margin:0!important;font-size:28px!important}.timer-center-head p{margin:5px 0 0;color:var(--muted);font-size:13px}.timer-center-head .standalone-timer-close{position:static!important}.timer-center-body{min-height:0;display:grid;grid-template-rows:minmax(0,1fr) auto;gap:13px;margin-top:13px}.timer-center-grid{min-height:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));gap:10px}.timer-center-item{border:1px solid #e0e7e1;background:#f9fbf9;border-radius:17px;padding:12px 13px;display:grid;grid-template-rows:auto 1fr auto;min-height:0}.timer-center-tag{display:flex;align-items:center;justify-content:space-between;gap:8px}.timer-center-tag span{font-size:11px;color:#2e7950;background:#eaf5ed;border-radius:999px;padding:5px 8px;max-width:72%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timer-center-tag small{font-size:10px;color:#7d887f}.timer-center-time{align-self:center;font-size:34px;line-height:1;text-align:center;font-weight:880;font-variant-numeric:tabular-nums;color:#173d2a}.timer-center-item.finished .timer-center-time{color:#a24c43}.timer-center-actions{display:grid;grid-template-columns:1fr 1fr;gap:7px}.timer-center-actions button{appearance:none;border:0;background:#edf3ee;border-radius:11px;min-height:38px;font-size:12px;font-weight:820;color:#315d43}.timer-center-actions button.primary{background:#2f8b5b;color:#fff}.timer-center-actions button.danger{background:#f7ece9;color:#9b4c43}.timer-center-empty{grid-column:1/-1;grid-row:1/-1;display:grid;place-items:center;text-align:center;border:1px dashed #d6dfd7;border-radius:18px;color:#738078;font-size:15px}.timer-center-quick{border-top:1px solid #e5eae6;padding-top:11px;display:grid;grid-template-columns:auto repeat(6,minmax(70px,1fr));gap:8px;align-items:center}.timer-center-quick strong{font-size:13px;color:#314f3d;white-space:nowrap}.timer-center-quick button{appearance:none;border:0;background:#f1f5f2;border-radius:12px;min-height:46px;font-size:13px;font-weight:820;color:#315c43}.timer-center-quick button.primary{background:#2f8b5b;color:#fff}
@media(max-height:700px) and (orientation:landscape){.r49-dash-head h2{font-size:26px}.r49-current{padding:14px 16px}.r49-current-title h3{font-size:26px}.r49-step-copy{font-size:21px;-webkit-line-clamp:2}.r49-main-timer-copy strong{font-size:25px}.r49-side-card{padding:10px 12px}.r49-next-copy strong{font-size:19px}.r49-quickbar button{min-height:52px}.timer-center-card{height:92vh}.timer-center-time{font-size:29px}}
.mock-menu-open{appearance:none;border:0;background:transparent;text-align:left;padding:0;min-width:0;color:inherit}.mock-menu-actions{display:flex;align-items:center;gap:6px}.mock-menu-remove{appearance:none;border:1px solid #ead8d3;background:#fffafa;color:#9b5548;border-radius:12px;min-height:36px;padding:7px 10px;font-size:12px;font-weight:800}

/* R40 · homepage primary CTA copy: 开工烧饭 */
.today-remove-confirm-card{width:min(430px,92vw)!important;padding:26px!important}
.today-remove-confirm-card h2{margin:0!important;font-size:25px!important;color:#173a28!important}
.today-remove-confirm-card p{margin:10px 0 0!important;color:#6d7870!important;font-size:15px!important;line-height:1.55!important}
.today-remove-name{margin-top:18px!important;padding:15px 16px!important;border-radius:16px!important;background:#f5f8f5!important;font-size:19px!important;font-weight:850!important;color:#183d2a!important}
.today-remove-status{min-height:20px;margin-top:9px;color:#a14b42;font-size:13px}
.today-remove-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px}
.today-remove-actions button{appearance:none;min-height:52px;border-radius:15px;font-size:16px;font-weight:800}
.today-remove-cancel{border:1px solid #e0e6e1;background:#fff;color:#526057}
.today-remove-confirm{border:0;background:#b84e45;color:#fff}.today-remove-confirm:disabled{opacity:.55}
.mock-menu-remove{position:relative;z-index:2}

.standalone-timer-card{width:min(500px,92vw)!important;padding:27px 28px 25px!important}
.standalone-timer-head{justify-content:center!important;position:relative!important;margin-bottom:18px!important}
.standalone-timer-head h2{font-size:27px!important;text-align:center!important}
.standalone-timer-close{position:absolute!important;right:-4px!important;top:-5px!important;width:38px!important;height:38px!important;border:0!important;background:transparent!important;color:#68746c!important}
.kitchen-timer-ui{display:grid;gap:17px}.kitchen-timer-mode{display:grid;grid-template-columns:1fr 1fr;background:#f0f3f1;border-radius:13px;padding:4px}
.kitchen-timer-mode button{appearance:none;border:0;background:transparent;border-radius:10px;min-height:42px;font-size:15px;font-weight:780;color:#69756d}.kitchen-timer-mode button.active{background:#fff;color:#246f4b;box-shadow:0 2px 10px rgba(30,58,41,.07)}
.kitchen-timer-presets{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.kitchen-timer-preset{appearance:none;border:0;background:#f3f5f3;border-radius:13px;min-height:48px;font-size:15px;font-weight:780;color:#3f4d44}.kitchen-timer-preset.active{background:#2f8b5b;color:#fff}
.kitchen-timer-big{font-size:64px;line-height:1;text-align:center;font-weight:880;letter-spacing:.02em;font-variant-numeric:tabular-nums;color:#193b29;padding:9px 0 2px}.kitchen-timer-sub{text-align:center;color:#7a857e;font-size:13px;margin-top:-7px}
.kitchen-timer-main{appearance:none;border:0;background:#2f8b5b;color:#fff;border-radius:15px;min-height:58px;font-size:18px;font-weight:850}.kitchen-timer-secondary{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.kitchen-timer-secondary button{appearance:none;border:1px solid #e0e6e1;background:#fff;border-radius:13px;min-height:46px;font-size:14px;font-weight:760;color:#48564d}.kitchen-timer-secondary button.danger{color:#a34b43}.kitchen-timer-main.danger{background:#b84e45}

.mock-cook-layout{display:grid;grid-template-columns:48px minmax(0,1fr) 48px;gap:10px;align-items:stretch}.mock-cook-center{min-width:0;display:grid;gap:14px;grid-column:2}.mock-side-nav{position:sticky;top:34%;align-self:start;appearance:none;width:44px;height:clamp(118px,19vh,168px);border:1px solid #dde6df;background:rgba(255,255,255,.97);border-radius:18px;color:#2d7650;font-size:34px;line-height:1;font-weight:650;box-shadow:0 8px 20px rgba(28,52,38,.07);z-index:8;display:flex;align-items:center;justify-content:center;padding:0}.mock-side-nav:active{transform:scale(.97)}.mock-side-nav:disabled{opacity:.24}.mock-side-nav.prev{grid-column:1}.mock-side-nav.next{grid-column:3}.mock-cook-actions{display:none!important}
@media(max-width:760px){.mock-cook-layout{grid-template-columns:43px minmax(0,1fr) 43px;gap:6px}.mock-side-nav{width:40px;height:clamp(108px,18vh,148px);border-radius:15px;font-size:30px}.kitchen-timer-big{font-size:58px}.standalone-timer-card{padding:24px 20px!important}}


/* R46: food manager hierarchy + stronger touch navigation */
.mock-welcome{grid-template-columns:1fr!important}.mock-feature-icon svg{width:27px;height:27px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}.mock-feature-icon{font-size:0!important}.mock-tip{display:none!important}
.mock-cook-layout{grid-template-columns:50px minmax(0,1fr) 50px!important;gap:10px!important}.mock-side-nav{width:46px!important;height:clamp(300px,48vh,420px)!important;border-radius:18px!important}.mock-head-home{appearance:none;border:1px solid #dfe7e1;background:#fff;color:#2d7650;border-radius:15px;min-height:46px;padding:10px 16px;font-size:15px;font-weight:800;white-space:nowrap}.mock-head-home:active{transform:scale(.98)}
.food-tabs{display:grid!important;grid-template-columns:repeat(4,1fr)!important;gap:10px!important;background:transparent!important;border:0!important;padding:0!important;margin-bottom:18px!important;overflow:visible!important}.food-tab{min-height:58px!important;border:1px solid #dfe7e1!important;border-radius:17px!important;background:#fff!important;color:#56655b!important;font-size:15px!important;font-weight:800!important;box-shadow:0 4px 14px rgba(30,54,40,.035)!important}.food-tab-primary{color:#256f4b!important;background:#f1f8f3!important;border-color:#cfe2d5!important}.food-tab.active{background:#2f8058!important;border-color:#2f8058!important;color:#fff!important;box-shadow:0 7px 18px rgba(47,128,88,.16)!important}.food-overview-card,.food-recommend-card,.food-log-card{padding:24px!important}.food-section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.food-inline-action{appearance:none;border:1px solid #cfe1d4;background:#f3f8f4;color:#2a7650;border-radius:14px;min-height:42px;padding:8px 14px;font-size:14px;font-weight:800;white-space:nowrap}.food-ranked-list{margin-top:16px!important}.food-ranked-row{display:grid!important;grid-template-columns:36px minmax(0,1fr) auto!important}.food-rank{width:30px;height:30px;border-radius:50%;background:#e8f4eb;color:#2c7850;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900}.food-recommend-tag{font-size:13px;color:#68756c!important}.food-log-entry{appearance:none;width:100%;margin-top:14px;border:1px solid #dfe7e1;background:#fff;border-radius:22px;min-height:82px;padding:16px 18px;display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:13px;text-align:left;box-shadow:0 6px 22px rgba(30,54,40,.04)}.food-log-entry-icon{width:40px;height:40px;border-radius:13px;background:#eef6f0;color:#2d7650;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:900}.food-log-entry strong{display:block;font-size:17px}.food-log-entry small{display:block;margin-top:4px;color:#748078;font-size:13px}.food-log-entry b{font-size:28px;color:#94a097}.food-log-filters{display:grid;grid-template-columns:180px minmax(0,1fr) auto;gap:10px;align-items:end;margin-top:18px}.food-log-count{font-size:13px;color:#78827b;margin-top:16px}.food-close{background:#2f8058!important;color:#fff!important;border-color:#2f8058!important;min-height:48px!important;padding:10px 19px!important;font-size:15px!important}
@media(max-width:760px){.mock-cook-layout{grid-template-columns:46px minmax(0,1fr) 46px!important;gap:7px!important}.mock-side-nav{width:43px!important;height:clamp(260px,45vh,360px)!important;border-radius:16px!important}.food-tabs{grid-template-columns:repeat(2,1fr)!important}.food-tab{min-height:56px!important}.food-log-filters{grid-template-columns:1fr!important}.mock-head-home{min-height:44px;padding:8px 12px}.food-section-head{align-items:center}}

/* R45: unified daily inventory consumption at finish */
/* R46: food manager information hierarchy, log search, icon/nav polish */
.mock-cook-head{display:flex;align-items:center;justify-content:space-between;gap:14px}.mock-cook-menu-back{appearance:none;border:0;background:#2f8b5b;color:#fff;border-radius:17px;min-height:54px;min-width:156px;padding:12px 20px;font-size:17px;font-weight:880;white-space:nowrap;box-shadow:0 9px 22px rgba(47,139,91,.22);letter-spacing:.01em}.mock-cook-menu-back:before{content:'←';display:inline-block;margin-right:8px;font-size:20px;vertical-align:-1px}.mock-cook-menu-back:active{transform:scale(.98)}.mock-recipe-consume-button{appearance:none;width:100%;margin-top:16px;border:0;border-radius:18px;min-height:62px;padding:14px 18px;background:var(--accent);color:#fff;font-size:18px;font-weight:820;box-shadow:0 10px 22px rgba(37,107,75,.14)}.mock-inline-actions-single{grid-template-columns:1fr!important}.consumption-page{max-width:900px;margin:0 auto}.consumption-list{display:grid;gap:12px}.consumption-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:18px;border:1px solid var(--line);background:var(--card-solid);border-radius:20px;padding:18px 20px}.consumption-copy{display:grid;gap:5px;min-width:0}.consumption-copy strong{font-size:20px}.consumption-copy small{font-size:14px;color:var(--muted);line-height:1.35}.consumption-control{display:grid;grid-template-columns:48px 82px auto 48px;align-items:center;gap:8px}.consumption-control button{appearance:none;border:1px solid var(--line);background:#fff;border-radius:14px;min-height:48px;font-size:24px;font-weight:760}.consumption-control input{width:82px;min-height:48px;border:1px solid var(--line);border-radius:14px;text-align:center;font-size:19px;font-weight:760;background:#fff}.consumption-unit{font-size:15px;color:var(--muted);min-width:22px}.consumption-actions{display:grid;grid-template-columns:minmax(160px,.45fr) minmax(280px,1fr);gap:12px;margin-top:18px}@media(max-width:720px){.consumption-row{grid-template-columns:1fr}.consumption-control{grid-template-columns:48px 82px auto 48px;justify-content:start}.consumption-actions{grid-template-columns:1fr}.mock-cook-menu-back{min-height:44px;padding:8px 12px;font-size:15px}}

/* R48: food log filter layout hard fix + R47 inventory browser */
.mock-cook-layout{grid-template-columns:58px minmax(0,1fr) 58px!important;gap:12px!important}
.mock-side-nav{position:fixed!important;top:50%!important;transform:translateY(-50%)!important;width:52px!important;height:clamp(300px,46vh,420px)!important;z-index:64!important;align-self:auto!important;margin:0!important}
.mock-side-nav.prev{left:max(10px,calc((100vw - 1120px)/2 + 18px))!important}.mock-side-nav.next{right:max(10px,calc((100vw - 1120px)/2 + 18px))!important}.mock-side-nav:active{transform:translateY(-50%) scale(.98)!important}
.food-inventory-browser{padding:24px!important}.food-inventory-count{font-size:14px;color:#6f7c73;font-weight:750;white-space:nowrap}.food-inventory-toolbar{display:grid;gap:10px;margin-top:18px}.food-inventory-toolbar>*{min-width:0}.food-inventory-filter-row{display:grid;grid-template-columns:minmax(260px,2fr) minmax(150px,.8fr) minmax(180px,.9fr);gap:10px;align-items:end}.food-inventory-filter-row>*{min-width:0}.food-inventory-sort-row{display:grid;grid-template-columns:minmax(220px,1fr) minmax(130px,.55fr);gap:10px;align-items:end;width:min(100%,520px)}.food-inventory-sort-row>*{min-width:0}.food-inventory-table-head{display:grid;grid-template-columns:minmax(260px,2fr) 120px 160px 110px;gap:14px;padding:12px 14px 9px;margin-top:16px;border-bottom:1px solid #e6ebe7;color:#859088;font-size:12px;font-weight:800;letter-spacing:.02em}.food-inventory-list{display:grid}.food-inventory-row{display:grid;grid-template-columns:minmax(260px,2fr) 120px 160px 110px;gap:14px;align-items:center;min-height:72px;padding:12px 14px;border-bottom:1px solid #edf1ee}.food-inventory-row:last-child{border-bottom:0}.food-inventory-main{min-width:0;display:grid;gap:5px}.food-inventory-main strong{font-size:17px;color:#213b2b}.food-inventory-main small{font-size:12px;color:#758078;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.food-inventory-name-line{display:flex;align-items:center;gap:10px;min-width:0}.food-inventory-name-line strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.food-inventory-rename{appearance:none;border:1px solid #d9e5dc;background:#f6faf7;color:#2c7650;border-radius:11px;min-width:60px;min-height:40px;padding:7px 11px;font-size:13px;font-weight:820;flex:0 0 auto}.food-edit-modal{z-index:120!important}.food-edit-card{width:min(520px,94vw)!important}.food-edit-sub{margin:-4px 0 18px;color:var(--muted);font-size:14px;line-height:1.5}.food-edit-fields{display:grid;gap:14px}.food-edit-quantity-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:end}.food-edit-unit{min-width:66px;min-height:50px;border-radius:14px;background:#f0f4f0;display:flex;align-items:center;justify-content:center;font-weight:820;color:#41604c}.food-edit-note{margin-top:12px;padding:11px 13px;border-radius:14px;background:#f4f7f4;color:#647067;font-size:13px;line-height:1.45}.finish-page.private-save-page{padding-bottom:190px!important}.finish-page.private-save-page .private-list{padding-bottom:18px}.finish-page.private-save-page .finish-modern-actions{position:fixed!important;left:50%!important;transform:translateX(-50%)!important;bottom:calc(env(safe-area-inset-bottom,0px) + 88px)!important;width:min(calc(100% - 44px),1060px)!important;z-index:76!important}.food-inventory-qty{font-size:17px;font-weight:850;color:#203c2b}.food-inventory-state{min-width:0}.food-inventory-priority{min-height:40px!important;padding:6px 9px!important;font-size:13px!important;border-radius:12px!important}.food-inventory-badge{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:6px 11px;border-radius:999px;font-size:13px;font-weight:820}.food-inventory-badge.status-ok{background:#e9f5ec;color:#2c7850}.food-inventory-badge.status-mid{background:#f7f1df;color:#876b21}.food-inventory-badge.status-low{background:#fbeae7;color:#a45045}.food-inventory-updated{font-size:13px;color:#748078;white-space:nowrap}.food-pagination{display:grid;grid-template-columns:120px 1fr 120px;gap:12px;align-items:center;margin-top:14px;padding-top:14px;border-top:1px solid #e6ebe7}.food-pagination span{text-align:center;color:#6e7a72;font-size:14px;font-weight:750}.food-pagination button{appearance:none;border:1px solid #dce5de;background:#fff;color:#2d7650;border-radius:13px;min-height:44px;font-size:14px;font-weight:800}.food-pagination button:disabled{opacity:.35}.food-status-compact{padding:20px 24px!important}.food-status-inline{display:grid;grid-template-columns:minmax(220px,1fr) 180px 170px;gap:10px;align-items:end;margin-top:14px}.food-status-inline>*{min-width:0}.food-log-filter-stack{display:flex;flex-direction:column;gap:14px;margin-top:18px;width:100%;min-width:0;overflow:hidden}.food-log-filter-row{display:block;width:100%;min-width:0;max-width:100%;overflow:hidden}.food-log-filter-row .food-label{display:flex;flex-direction:column;gap:7px;width:100%;min-width:0;max-width:100%;box-sizing:border-box}.food-log-filter-row .food-input{display:block;width:100%!important;min-width:0!important;max-width:100%!important;box-sizing:border-box!important;margin:0!important;overflow:hidden;text-overflow:ellipsis}#foodLogDate{width:100%!important;min-width:0!important;max-width:100%!important;box-sizing:border-box!important}.food-log-filter-actions{display:flex;justify-content:flex-start;margin-top:14px;width:100%}.food-log-filter-actions .food-secondary{min-width:140px;max-width:220px}.food-log-card{overflow:hidden}.mock-cook-head{padding:2px 5px 10px}.mock-cook-head>div{min-width:0}
@media(max-width:900px){.food-inventory-filter-row{grid-template-columns:1fr 1fr}.food-inventory-search{grid-column:1/-1}.food-inventory-sort-row{grid-template-columns:1fr 1fr;width:100%}.food-inventory-table-head{display:none}.food-inventory-row{grid-template-columns:minmax(0,1fr) auto;gap:8px 14px;border:1px solid #e6ebe7;border-radius:16px;margin-top:9px;padding:14px}.food-inventory-qty{grid-column:2;grid-row:1}.food-inventory-state{grid-column:1;grid-row:2}.food-inventory-updated{grid-column:2;grid-row:2;align-self:center}.food-status-inline{grid-template-columns:1fr 1fr}.food-status-inline .food-primary{grid-column:1/-1}.mock-side-nav.prev{left:7px!important}.mock-side-nav.next{right:7px!important}.mock-side-nav{width:46px!important;height:clamp(260px,43vh,360px)!important}.mock-cook-layout{grid-template-columns:48px minmax(0,1fr) 48px!important;gap:6px!important}}
@media(max-width:640px){.food-inventory-filter-row,.food-inventory-sort-row{grid-template-columns:1fr}.food-inventory-search{grid-column:auto}.food-pagination{grid-template-columns:96px 1fr 96px}.food-status-inline{grid-template-columns:1fr}.food-status-inline .food-primary{grid-column:auto}.food-inventory-updated{font-size:12px}.mock-side-nav{width:42px!important}.mock-cook-layout{grid-template-columns:44px minmax(0,1fr) 44px!important}}
</style>
</head>
<body>
<div id="app">
<header class="modern-header"><div class="brand-wrap"><span class="brand">小K</span><span class="brand-sub">今天想做点什么？</span></div><div class="modern-head-actions"><button id="qaHeaderBtn" type="button" class="header-voice"><span class="header-voice-icon">🎙</span><span><strong>问小K</strong><small>你说，我来帮你</small></span></button><button id="moreBtn" type="button" class="more-btn" aria-label="更多设置">•••</button><span id="dot" class="dot"></span></div></header>
<button id="foodOpenBtn" type="button" style="display:none" aria-hidden="true"></button>
<div id="timerStrip"></div>
<main><section id="mainPanel" class="panel"><div id="eyebrow" class="eyebrow">HOME AI · 厨房</div><h1 id="title" class="title">小K</h1><div id="message" class="message">正在读取 Gateway…</div><div id="content" class="content"></div><div id="nav" class="nav" style="display:none"></div></section></main>
<nav id="globalBottomNav" class="global-bottom-nav" aria-label="主导航"><button type="button" data-global-nav="home"><span class="nav-icon">⌂</span>首页</button><button type="button" data-global-nav="today"><span class="nav-icon">▣</span>今日菜谱</button><button type="button" data-global-nav="shopping"><span class="nav-icon">🛒</span>采购清单</button><button type="button" data-global-nav="food"><span class="nav-icon">🥬</span>食材管理</button></nav>
<div id="qaDock" class="qa-dock"><button id="qaBtn" type="button" class="qa-btn">🎙 问小K</button><div class="qa-copy"><div id="qaStatus" class="qa-status">可以问做法、替代食材、火候和补救办法</div><div id="qaTranscript" class="qa-transcript"></div><div id="qaAnswer" class="qa-answer"></div></div><button id="qaClear" class="qa-clear" aria-label="清除回答">×</button></div>
<footer id="footer">KitchenTerminal __KITCHEN_UI_VERSION__ · 等待 Gateway</footer>
</div>
<div id="moreModal" class="modal"><div class="modal-card utility-card"><div class="utility-head"><div><h2>小K 设置</h2><div class="utility-sub">连接、声音与帮助</div></div><button id="moreClose" type="button" class="utility-close">×</button></div><div class="utility-status"><span id="micState" class="status-pill wait">🎙 麦克风 检测中</span><span id="audioState" class="status-pill wait">🔊 语音 待激活</span><span class="status-pill"><span id="statusText">连接中</span></span></div><div class="utility-actions"><button id="audioRouteBtn" type="button" class="help-btn audio-route-btn">🔊 播放设备</button><button id="helpBtn" class="help-btn">操作指南</button></div><div class="utility-version">__KITCHEN_UI_VERSION__</div></div></div>
<div id="timerCenterModal" class="modal timer-center-modal"><div class="modal-card timer-center-card"><div class="timer-center-head"><div><h2>多计时器中心</h2><p>菜谱计时自动关联菜品和步骤</p></div><button id="timerCenterClose" type="button" class="standalone-timer-close" aria-label="关闭">×</button></div><div id="timerCenterHost"></div></div></div>
<div id="standaloneTimerModal" class="modal"><div class="modal-card standalone-timer-card"><div class="standalone-timer-head"><h2>厨房计时</h2><button id="standaloneTimerClose" type="button" class="standalone-timer-close" aria-label="关闭">×</button></div><div id="idleTimerHost"></div></div></div>
<div id="shoppingModal" class="modal"><div class="modal-card shopping-modal-card"><div class="shopping-modal-head"><div><h2>今日采购</h2><p>勾选后自动保存，关掉弹窗也不会丢。</p></div><button id="shoppingModalClose" type="button" class="shopping-modal-close" aria-label="关闭">×</button></div><div id="shoppingModalHost" class="shopping-modal-body"></div></div></div>
<div id="timerModal" class="modal timer-editor-modal"><div class="modal-card"><h2>设置计时</h2><div class="time-inputs"><input id="minInput" inputmode="numeric" pattern="[0-9]*" value="0"><span>:</span><input id="secInput" inputmode="numeric" pattern="[0-9]*" value="30"></div><div class="modal-actions"><button id="modalCancel">取消</button><button id="modalOK" class="primary">确定</button></div></div></div>
<div id="todayRemoveModal" class="modal"><div class="modal-card today-remove-confirm-card"><h2>从今日菜谱移除？</h2><p>只会从今天的 Obsidian 菜谱中移除，不会删除“私房菜”里的原始菜谱。</p><div id="todayRemoveName" class="today-remove-name"></div><div id="todayRemoveStatus" class="today-remove-status"></div><div class="today-remove-actions"><button id="todayRemoveCancel" type="button" class="today-remove-cancel">取消</button><button id="todayRemoveConfirm" type="button" class="today-remove-confirm">确认移除</button></div></div></div>
<div id="helpModal" class="modal"><div class="modal-card"><h2>小K 操作指南</h2><ol class="help-list"><li><strong>开始备菜：</strong>先进入“今日菜谱”，点击“开始备菜”，把当天所有菜的备菜项目一次完成并逐项勾选。</li><li><strong>正式烹饪：</strong>统一备菜全部完成后，从“今日菜谱”进入具体菜品，系统会直接从正式烹饪第1步开始。</li><li><strong>计时：</strong>步骤里可以按建议时间计时；等待首页另有独立计时器，不加载菜单也能直接使用。独立计时结束后会持续响铃，直到你主动结束提醒。</li><li><strong>问小K：</strong>点底部的大按钮开始说话，再点一次结束。可以问火候、替代食材、做法原因和翻车补救。</li><li><strong>语音播报：</strong>厨房 TTS 走 iPad 的系统媒体播放链。点顶部“播放设备”可选择 HomePod / AirPlay；未选择无线设备时由 iPad 当前系统输出播放。</li><li><strong>结束烹饪：</strong>在厨房中台点“结束今日厨房”，先进入“今日食材结算”核对实际消耗，再选择是否把喜欢的菜保存到私房菜。</li></ol><div class="help-note">顶部状态用于快速确认麦克风、厨房语音、AirPlay 播放目标和 Gateway 连接状态。麦克风录制仍使用独立采集链，不受语音输出设备切换影响。</div><div class="modal-actions" style="grid-template-columns:1fr"><button id="helpClose" class="primary">知道了</button></div></div></div>
<div id="foodModal" class="food-modal" aria-hidden="true"><div class="food-shell">
  <div class="food-head"><div><div class="food-title">食材管理</div><div class="food-sub">默认 3 人 · 份 / 个 / 自然单位 / 状态管理</div></div><button id="foodCloseBtn" type="button" class="food-close">完成</button></div>
  <div class="food-tabs"><button type="button" class="food-tab active" data-food-tab="home">概览</button><button type="button" class="food-tab food-tab-primary" data-food-tab="buy">＋ 买入食材</button><button type="button" class="food-tab food-tab-primary" data-food-tab="consume">− 记录消耗</button><button type="button" class="food-tab" data-food-tab="inventory">全部食材</button></div>

  <section id="foodViewHome" class="food-view active">
    <div class="food-card food-overview-card"><div class="food-section-head"><div><h3>当前状态</h3><div class="food-card-note">先看家里整体还有什么，再决定今天优先吃什么</div></div></div><div id="foodSummary" class="food-summary"></div></div>
    <div class="food-card food-recommend-card" style="margin-top:14px"><div class="food-section-head"><div><h3>推荐食用顺序</h3><div class="food-card-note">按当前建议窗口和入库时间排序，先显示前 10 项</div></div><button id="foodShowAllBtn" type="button" class="food-inline-action">显示全部</button></div><div id="foodRecommendedList" class="food-list food-ranked-list"></div></div>
    <button id="foodLogBtn" type="button" class="food-log-entry"><span class="food-log-entry-icon">≡</span><span><strong>食材日志</strong><small>按日期或具体食材查询买入、消耗和状态调整</small></span><b>›</b></button>
  </section>

  <section id="foodViewBuy" class="food-view">
    <div class="food-grid">
      <div class="food-card"><h3>这次在哪里买的？</h3><div class="food-card-note">先选来源，再直接拍订单 / 小票 / 食材。识别后确认一次即可入库。</div><div id="foodChannelGrid" class="food-channel-grid" style="margin-top:14px"><button type="button" class="food-channel active" data-source="菜市场">🥬<br>菜市场</button><button type="button" class="food-channel" data-source="网上APP">📱<br>网上APP</button><button type="button" class="food-channel" data-source="超市">🧾<br>超市</button></div><div id="foodChannelHint" class="food-note" style="margin-top:12px">菜市场：可以拍食材；网上APP：优先订单截图；超市：优先拍小票。</div><div class="food-scan-box"><div class="food-scan-title">📷 拍一下，自动入库</div><div class="food-scan-copy">支持订单截图、小票和一张照片里的多种食材。小K会尽量一次盘点全部项目，数量不准可直接修改后统一入库。</div><div class="food-scan-actions"><button id="foodScanBtn" type="button" class="food-scan-button">拍照 / 选择图片</button><button id="foodScanAddMissing" type="button" class="food-scan-button secondary">＋ 补一项</button><button id="foodScanClear" type="button" class="food-scan-button secondary" style="display:none">清空结果</button></div><input id="foodScanFile" class="food-scan-file" type="file" accept="image/*"><div id="foodScanStatus" class="food-scan-status">还没有选择图片</div><div id="foodScanResults" class="food-scan-results"></div><button id="foodScanCommit" type="button" class="food-primary food-scan-commit" style="display:none">全部确认入库</button></div></div>
      <div class="food-card"><h3>加入食材</h3><form id="foodAddForm" class="food-form" style="margin-top:14px"><label class="food-label">食材名称<input id="foodAddName" class="food-input" autocomplete="off" placeholder="例如：排骨"></label><div class="food-form-grid"><label class="food-label">分类<select id="foodAddCategory" class="food-select"><option>肉类</option><option>海鲜</option><option>蔬菜</option><option>蛋类</option><option>奶制品</option><option>包装食品</option><option>主食</option><option>其他</option></select></label><label class="food-label">单位<select id="foodAddUnit" class="food-select"><option>份</option><option>个</option><option>盒</option><option>瓶</option><option>包</option><option>杯</option><option>块</option><option>根</option><option>颗</option></select></label></div><label class="food-label">数量<input id="foodAddAmount" class="food-input" inputmode="decimal" value="1"></label><button type="submit" class="food-primary">加入食材</button></form></div>
    </div>
  </section>

  <section id="foodViewConsume" class="food-view">
    <div class="food-grid">
      <div class="food-card"><h3>记录消耗</h3><div class="food-card-note">以后菜单会自动预估扣减，这里保留最直接的人工入口</div><form id="foodConsumeForm" class="food-form" style="margin-top:14px"><label class="food-label">食材<select id="foodConsumeName" class="food-select"></select></label><label class="food-label">用了多少<input id="foodConsumeAmount" class="food-input" inputmode="decimal" value="1"></label><button type="submit" class="food-primary">记录这次消耗</button></form></div>
      <div class="food-card"><h3>我们要观察什么？</h3><div class="food-note" style="margin-top:14px">先观察你做完饭后，愿不愿意顺手点一次。真正舒服后，再让“今日菜单 → 完成烹饪”自动生成消耗建议。</div></div>
    </div>
  </section>

  <section id="foodViewInventory" class="food-view">
    <div class="food-card food-inventory-browser">
      <div class="food-section-head"><div><h3>全部食材</h3><div class="food-card-note">查询、筛选和分页查看家里当前所有剩余食材</div></div><div id="foodInventoryCount" class="food-inventory-count"></div></div>
      <div class="food-inventory-toolbar">
        <div class="food-inventory-filter-row">
          <label class="food-label food-inventory-search">查询食材<input id="foodInventorySearch" class="food-input" autocomplete="off" placeholder="输入食材名称、来源或存放位置"></label>
          <label class="food-label">分类<select id="foodInventoryCategory" class="food-select"><option value="">全部分类</option><option>肉类</option><option>海鲜</option><option>蔬菜</option><option>蛋类</option><option>奶制品</option><option>包装食品</option><option>主食</option><option>佐料/粮油</option><option>其他</option></select></label>
          <label class="food-label food-inventory-date">入库日期<input id="foodInventoryIntakeDate" class="food-input" type="date" aria-label="按最近入库日期筛选"></label>
        </div>
        <div class="food-inventory-sort-row">
          <label class="food-label">排序<select id="foodInventorySort" class="food-select"><option value="priority">推荐食用顺序</option><option value="intake">最近入库</option><option value="updated">最近更新</option><option value="category">按分类</option><option value="name">按名称</option></select></label>
          <label class="food-label">每页<select id="foodInventoryPageSize" class="food-select"><option value="10">10 项</option><option value="20">20 项</option><option value="30">30 项</option></select></label>
        </div>
      </div>
      <div class="food-inventory-table-head"><span>食材</span><span>剩余</span><span>建议 / 状态</span><span>最近更新</span></div>
      <div id="foodInventoryList" class="food-inventory-list"></div>
      <div class="food-pagination"><button id="foodInventoryPrev" type="button">上一页</button><span id="foodInventoryPageInfo">第 1 / 1 页</span><button id="foodInventoryNext" type="button">下一页</button></div>
    </div>
    <div class="food-card food-status-compact" style="margin-top:14px"><div class="food-section-head"><div><h3>佐料 / 粮油状态</h3><div class="food-card-note">这类食材只维护“充足 / 一般 / 快没了”</div></div></div><form id="foodStatusForm" class="food-status-inline"><label class="food-label">名称<input id="foodStatusName" class="food-input" autocomplete="off" placeholder="例如：料酒"></label><label class="food-label">状态<select id="foodStatusValue" class="food-select"><option>充足</option><option>一般</option><option>快没了</option></select></label><button type="submit" class="food-primary">更新状态</button></form></div>
  </section>

  <section id="foodViewLog" class="food-view">
    <div class="food-card food-log-card">
      <div class="food-section-head"><div><h3>食材日志</h3><div class="food-card-note">可以按日期和具体食材筛选买入、消耗和状态变化</div></div><button id="foodLogBack" type="button" class="food-inline-action">返回概览</button></div>
      <div class="food-log-filter-stack"><div class="food-log-filter-row"><label class="food-label">日期<input id="foodLogDate" class="food-input" type="date"></label></div><div class="food-log-filter-row"><label class="food-label">食材查询<input id="foodLogName" class="food-input" autocomplete="off" placeholder="例如：鸡蛋 / 排骨"></label></div></div>
      <div class="food-log-filter-actions"><button id="foodLogClear" type="button" class="food-secondary">清除筛选</button></div>
      <div id="foodLogCount" class="food-log-count"></div>
      <div id="foodLogList" class="food-list"></div>
    </div>
  </section>
</div></div>
<div id="foodEditModal" class="modal food-edit-modal"><div class="modal-card food-edit-card"><h2>编辑食材</h2><div class="food-edit-sub">可以修正识别错误的名称，也可以直接盘点当前实际库存。</div><div class="food-edit-fields"><label class="food-label">食材名称<input id="foodEditName" class="food-input" autocomplete="off"></label><div id="foodEditQuantityRow" class="food-edit-quantity-row"><label class="food-label">当前库存数量<input id="foodEditQuantity" class="food-input" type="number" inputmode="decimal" min="0"></label><div id="foodEditUnit" class="food-edit-unit"></div></div></div><div id="foodEditNote" class="food-edit-note">数量修改属于库存盘点，不会记成烹饪消耗。坏掉、丢弃或之前登记错了，都可以直接改成实际剩余数量。</div><div class="modal-actions"><button id="foodEditCancel" type="button">取消</button><button id="foodEditSave" type="button" class="primary">保存修改</button></div></div></div>
<div id="foodToast" class="food-toast" aria-live="polite"></div>
<audio id="kitchenPlayer" preload="auto" playsinline x-webkit-airplay="allow" aria-hidden="true" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;left:-9999px;top:-9999px"></audio>
<script>
(function(){
  var ws=null,retry=1000,httpOK=false,wsOK=false,lastRevision='',currentView=null,latestTimers=[],drafts={},modalTarget=null;
  var kitchenPlayer=null,audioUnlocked=false,audioMuted=false,lastAudioId='',pendingAudio=null,audioPlaying=false,audioWireless=false,audioRouteAvailable=false,standaloneAlarmId='';
  var qaRecording=false,qaBusy=false,qaStream=null,qaCtx=null,qaSource=null,qaProcessor=null,qaChunks=[],qaSampleRate=0,qaStartedAt=0,qaAutoStop=null,qaSocket=null;
  var kitchenProtocol='__KITCHEN_PROTOCOL__',qaMaxMs=__KITCHEN_QA_MAX_MS__;
  var deviceId='KitchenTerminal-iPadMini';
  try{var saved=localStorage.getItem('kitchen.device_id');if(saved){deviceId=saved;}else{deviceId='KitchenTerminal-iPadMini-'+String(Date.now()).slice(-6);localStorage.setItem('kitchen.device_id',deviceId);}}catch(e){}
  var $=function(id){return document.getElementById(id);};
  function setStatus(){var ok=httpOK||wsOK;$('dot').className='dot'+(ok?' online':'');$('statusText').textContent=wsOK?'实时连接':(httpOK?'已连接':'正在重连');}
  function clearView(){$('content').innerHTML='';$('nav').innerHTML='';$('nav').style.display='none';$('nav').style.gridTemplateColumns='1fr 1fr 1fr';}
  function el(tag,cls,text){var x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined&&text!==null)x.textContent=String(text);return x;}
  function xhrGet(url,cb){var x=new XMLHttpRequest();x.open('GET',url+(url.indexOf('?')>=0?'&':'?')+'_='+Date.now(),true);x.onreadystatechange=function(){if(x.readyState!==4)return;if(x.status>=200&&x.status<300){httpOK=true;setStatus();cb(null,x.responseText);}else{httpOK=false;setStatus();cb(new Error('HTTP '+x.status),x.responseText||'');}};x.onerror=function(){httpOK=false;setStatus();cb(new Error('network'),'');};x.send(null);}
  function showError(text){$('message').textContent=text;$('footer').textContent='KitchenTerminal __KITCHEN_UI_VERSION__ · 页面错误';}
  function micStatusState(){var b=$('micState');if(!b)return;var secure=!!window.isSecureContext,gum=!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia);if(secure&&gum){b.textContent='🎙 麦克风 已就绪';b.className='status-pill ready';}else{b.textContent='🎙 麦克风 不可用';b.className='status-pill bad';}}
  var audioPrimeSrc='data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==';
  function initKitchenPlayer(){if(kitchenPlayer)return kitchenPlayer;kitchenPlayer=$('kitchenPlayer');if(!kitchenPlayer)return null;try{kitchenPlayer.setAttribute('x-webkit-airplay','allow');}catch(e){}try{audioWireless=!!kitchenPlayer.webkitCurrentPlaybackTargetIsWireless;}catch(e){audioWireless=false;}kitchenPlayer.addEventListener('webkitcurrentplaybacktargetiswirelesschanged',function(){try{audioWireless=!!kitchenPlayer.webkitCurrentPlaybackTargetIsWireless;}catch(e){audioWireless=false;}audioButtonState();},false);kitchenPlayer.addEventListener('webkitplaybacktargetavailabilitychanged',function(ev){audioRouteAvailable=!!(ev&&String(ev.availability||'')==='available');audioButtonState();},false);return kitchenPlayer;}
  function audioButtonState(){var b=$('audioState'),r=$('audioRouteBtn');if(!b)return;if(audioMuted){b.textContent='🔇 语音 已静音';b.className='status-pill bad';}else if(audioWireless){b.textContent='📡 AirPlay 已连接';b.className='status-pill ready';}else if(audioUnlocked){b.textContent='🔊 语音 已就绪';b.className='status-pill ready';}else{b.textContent='🔊 语音 待激活';b.className='status-pill wait';}if(r){r.className='help-btn audio-route-btn'+(audioWireless?' wireless':'');r.textContent=audioWireless?'📡 AirPlay':'🔊 播放设备';}}
  function finishAudioUnlock(){audioUnlocked=true;audioMuted=false;try{localStorage.setItem('kitchen.audio_enabled','1');localStorage.setItem('kitchen.audio_muted','0');}catch(e){}audioButtonState();if(pendingAudio)playKitchenAudio(pendingAudio);}
  function unlockAudio(){try{var p=initKitchenPlayer();if(!p)throw new Error('HTMLAudioElement unavailable');if(audioUnlocked){audioButtonState();return;}p.onended=null;p.onerror=null;p.src=audioPrimeSrc;p.load();var started=p.play();if(started&&started.then){started.then(function(){try{p.pause();try{p.currentTime=0;}catch(e){}}catch(e){}finishAudioUnlock();}).catch(function(err){var b=$('audioState');if(b){b.textContent='🔇 语音 待授权';b.className='status-pill bad';}console.log('kitchen media unlock failed',err);});}else{try{p.pause();try{p.currentTime=0;}catch(e){}}catch(e){}finishAudioUnlock();}}catch(e){var b=$('audioState');if(b){b.textContent='🔇 语音 不可用';b.className='status-pill bad';}showError('无法开启厨房语音：'+String(e));}}
  function pickAudioOutput(ev){if(ev){try{ev.preventDefault();ev.stopPropagation();}catch(e){}}var p=initKitchenPlayer();if(!p)return;unlockAudio();if(typeof p.webkitShowPlaybackTargetPicker==='function'){try{p.webkitShowPlaybackTargetPicker();return;}catch(e){console.log('AirPlay picker failed',e);}}$('qaStatus').textContent='当前浏览器没有提供网页内播放设备选择器，可在 iPad 控制中心选择 AirPlay。';}
  try{audioMuted=false;localStorage.setItem('kitchen.audio_muted','0');}catch(e){}initKitchenPlayer();micStatusState();audioButtonState();
  function opportunisticUnlock(ev){if(ev&&ev.target&&ev.target.id==='audioRouteBtn')return;if(!audioUnlocked)unlockAudio();}
  document.addEventListener('click',opportunisticUnlock,false);
  $('audioRouteBtn').addEventListener('click',pickAudioOutput,false);
  function openHelp(){$('helpModal').className='modal show';}
  function closeHelp(){$('helpModal').className='modal';}
  function openMore(){$('moreModal').className='modal show';}
  function closeMore(){$('moreModal').className='modal';}
  $('helpBtn').onclick=function(){closeMore();openHelp();};$('helpClose').onclick=closeHelp;$('moreBtn').onclick=openMore;$('moreClose').onclick=closeMore;

  /* Home Food A0.1: Gateway-owned SQLite data, deliberately simple iPad flow. */
  var foodState={items:[],priority:[],needs_attention:[],events:[],default_people:3},foodSource='菜市场',foodToastTimer=null,foodScanSocket=null,foodScanItems=[],foodScanBusy=false;var foodInventoryPage=1,foodInventoryPageSize=10;
  function foodToast(text){var n=$('foodToast');if(!n)return;n.textContent=String(text||'');n.className='food-toast show';if(foodToastTimer)clearTimeout(foodToastTimer);foodToastTimer=setTimeout(function(){n.className='food-toast';},2200);}
  function foodXHR(url,cb){var x=new XMLHttpRequest(),settled=false;function finish(err,data){if(settled)return;settled=true;cb(err,data);}x.open('GET',url+(url.indexOf('?')>=0?'&':'?')+'_='+Date.now(),true);x.timeout=12000;x.onreadystatechange=function(){if(x.readyState!==4)return;var data=null;try{data=JSON.parse(x.responseText||'{}');}catch(e){}if(x.status>=200&&x.status<300&&data&&data.ok!==false){finish(null,data);}else{finish(new Error((data&&data.error)||('HTTP '+x.status)),data);}};x.onerror=function(){finish(new Error('network'));};x.ontimeout=function(){finish(new Error('保存超时，请重试'));};x.send(null);}
  function foodAction(name,params,done){var u='/kitchen/food/action?action='+encodeURIComponent(name),k;params=params||{};for(k in params){if(params.hasOwnProperty(k)&&params[k]!==undefined&&params[k]!==null&&params[k]!=='')u+='&'+encodeURIComponent(k)+'='+encodeURIComponent(String(params[k]));}foodXHR(u,function(err,data){if(err){foodToast('没有保存：'+err.message);if(done)done(err);return;}foodState=data;if(done)done(null,data);try{renderFood();}catch(e){try{console.error('[FOOD-RENDER]',e);}catch(_e){}setTimeout(loadFood,80);}});}
  function loadFood(){foodXHR('/kitchen/food/view',function(err,data){if(err){foodToast('食材数据读取失败');return;}foodState=data;renderFood();});}
  function openFood(tab){$('foodModal').className='food-modal show';$('foodModal').setAttribute('aria-hidden','false');updateGlobalNav('food');foodSwitch(tab||'home');loadFood();}
  function closeFood(){$('foodModal').className='food-modal';$('foodModal').setAttribute('aria-hidden','true');updateGlobalNav(navActiveForView(currentView));}
  function foodSwitch(tab){var tabs=document.querySelectorAll('.food-tab'),views=document.querySelectorAll('.food-view'),i;for(i=0;i<tabs.length;i++)tabs[i].className='food-tab'+(tabs[i].getAttribute('data-food-tab')===tab?' active':'')+(tabs[i].classList.contains('food-tab-primary')?' food-tab-primary':'');for(i=0;i<views.length;i++)views[i].className='food-view'+(views[i].id==='foodView'+tab.charAt(0).toUpperCase()+tab.slice(1)?' active':'');if(tab==='consume')renderFoodConsume();if(tab==='inventory'){foodInventoryPage=1;renderFoodInventory();}if(tab==='log')renderFoodLog();}
  $('foodOpenBtn').onclick=function(){openFood('home');};$('foodCloseBtn').onclick=closeFood;
  (function(){var tabs=document.querySelectorAll('.food-tab'),i;for(i=0;i<tabs.length;i++)tabs[i].onclick=function(){foodSwitch(this.getAttribute('data-food-tab'));};})();
  function foodUnitText(item){if(item.unit==='状态')return item.status||'一般';return String(item.quantity)+' '+item.unit;}
  function foodEmpty(host,text){host.innerHTML='';host.appendChild(el('div','food-empty',text));}
  function foodRow(item,extra){var row=el('div','food-row'),main=el('div','food-row-main');main.appendChild(el('div','food-row-name',item.name));var meta=[item.category];if(item.source)meta.push(item.source);if(extra)meta.push(extra);main.appendChild(el('div','food-row-meta',meta.join(' · ')));row.appendChild(main);row.appendChild(el('div','food-row-value',foodUnitText(item)));return row;}
  function foodScanSetStatus(text,busy){var n=$('foodScanStatus');if(!n)return;n.textContent=text||'';n.className='food-scan-status'+(busy?' busy':'');}
  function foodScanReset(){foodScanItems=[];var h=$('foodScanResults');if(h)h.innerHTML='';if($('foodScanCommit'))$('foodScanCommit').style.display='none';if($('foodScanClear'))$('foodScanClear').style.display='none';foodScanSetStatus('还没有选择图片',false);}
  function foodScanAddMissing(){foodScanItems.push({name:'',category:'其他',mode:'quantity',amount:1,unit:'份',status:'',raw:'手动补充',confidence:1});renderFoodScanResults();foodScanSetStatus('已补一项，请填写名称和数量',false);var inputs=document.querySelectorAll('.food-scan-name');if(inputs.length){var n=inputs[inputs.length-1];try{n.focus();}catch(e){}}}
  function foodScanCategorySelect(value){var sel=document.createElement('select');sel.className='food-select food-scan-category';var opts=['肉类','海鲜','蔬菜','蛋类','奶制品','包装食品','主食','佐料/粮油','其他'];for(var i=0;i<opts.length;i++){var o=document.createElement('option');o.value=opts[i];o.textContent=opts[i];if(opts[i]===value)o.selected=true;sel.appendChild(o);}return sel;}
  function foodScanUnitSelect(value,statusMode){var sel=document.createElement('select');sel.className='food-select food-scan-unit';var opts=statusMode?['状态']:['份','个','盒','瓶','包','杯','块','根','颗','袋'];for(var i=0;i<opts.length;i++){var o=document.createElement('option');o.value=opts[i];o.textContent=opts[i];if(opts[i]===value)o.selected=true;sel.appendChild(o);}return sel;}
  function renderFoodScanResults(){var host=$('foodScanResults');if(!host)return;host.innerHTML='';for(var i=0;i<foodScanItems.length;i++){(function(idx,item){var row=el('div','food-scan-row'),head=el('div','food-scan-row-head'),name=document.createElement('input');name.className='food-scan-name';name.value=item.name||'';name.setAttribute('aria-label','食材名称');name.oninput=function(){item.name=this.value;};head.appendChild(name);var rm=el('button','food-scan-remove','×');rm.type='button';rm.onclick=function(){foodScanItems.splice(idx,1);renderFoodScanResults();};head.appendChild(rm);row.appendChild(head);var fields=el('div','food-scan-fields'),cat=foodScanCategorySelect(item.category||'其他');cat.onchange=function(){item.category=this.value;if(this.value==='佐料/粮油'){item.mode='status';item.unit='状态';item.status='充足';}renderFoodScanResults();};fields.appendChild(cat);if(item.mode==='status'||item.unit==='状态'){var st=document.createElement('select');st.className='food-select';['充足','一般','快没了'].forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v;if(v===(item.status||'充足'))o.selected=true;st.appendChild(o);});st.onchange=function(){item.status=this.value;};fields.appendChild(st);fields.appendChild(foodScanUnitSelect('状态',true));}else{var amount=document.createElement('input');amount.className='food-input';amount.type='number';amount.min='0.25';amount.step='0.25';amount.value=String(item.amount||1);amount.oninput=function(){item.amount=parseFloat(this.value)||1;};fields.appendChild(amount);var unit=foodScanUnitSelect(item.unit||'份',false);unit.onchange=function(){item.unit=this.value;};fields.appendChild(unit);}row.appendChild(fields);if(item.raw)row.appendChild(el('div','food-scan-raw','识别自：'+item.raw));host.appendChild(row);})(i,foodScanItems[i]);}var has=foodScanItems.length>0;$('foodScanCommit').style.display=has?'block':'none';$('foodScanClear').style.display=has?'inline-block':'none';if(!has&&!foodScanBusy)foodScanSetStatus('没有待确认项目',false);}
  function foodScanReadBlob(blob,ok,fail){var r=new FileReader();r.onerror=function(){if(fail)fail();};r.onload=function(){ok(r.result);};r.readAsArrayBuffer(blob);}
  function foodScanPrepare(file,ok,fail){var passthrough=function(){foodScanReadBlob(file,function(buf){ok(buf,file.type||'image/jpeg',false);},fail);};if(file.size<=1600*1024){passthrough();return;}var fr=new FileReader();fr.onerror=passthrough;fr.onload=function(){var img=new Image();img.onerror=passthrough;img.onload=function(){try{var textSource=(foodSource==='网上APP'||foodSource==='超市'),maxW=textSource?1800:2200,maxH=textSource?10000:2200,w=img.naturalWidth||img.width,h=img.naturalHeight||img.height,scale=Math.min(1,maxW/Math.max(w,1),maxH/Math.max(h,1)),cw=Math.max(1,Math.round(w*scale)),ch=Math.max(1,Math.round(h*scale)),canvas=document.createElement('canvas');canvas.width=cw;canvas.height=ch;var ctx=canvas.getContext('2d');if(!ctx){passthrough();return;}ctx.fillStyle='#fff';ctx.fillRect(0,0,cw,ch);ctx.drawImage(img,0,0,cw,ch);canvas.toBlob(function(blob){if(!blob){passthrough();return;}foodScanReadBlob(blob,function(buf){ok(buf,'image/jpeg',true);},passthrough);},'image/jpeg',textSource?0.92:0.88);}catch(e){passthrough();}};img.src=fr.result;};fr.readAsDataURL(file);}
  function foodScanSendChunks(sock,buf,rid,onDone,onFail){var total=buf.byteLength||0,offset=0,chunkSize=256*1024,maxBuffered=768*1024,lastPct=-1,stopped=false;function fail(msg){if(stopped)return;stopped=true;if(onFail)onFail(msg||'发送图片失败');}function pump(){if(stopped)return;if(!sock||sock.readyState!==WebSocket.OPEN){fail('识别连接中断，请重试');return;}try{while(offset<total&&sock.bufferedAmount<maxBuffered){var end=Math.min(offset+chunkSize,total);sock.send(buf.slice(offset,end));offset=end;}var pct=total?Math.min(100,Math.floor(offset*100/total)):100;if(pct!==lastPct){lastPct=pct;foodScanSetStatus('正在上传图片… '+pct+'%',true);}if(offset>=total){if(sock.bufferedAmount>64*1024){setTimeout(pump,18);return;}sock.send(JSON.stringify({type:'kitchen.food.scan.stop',request_id:rid}));stopped=true;foodScanSetStatus('图片上传完成，正在识别…',true);if(onDone)onDone();return;}setTimeout(pump,12);}catch(e){fail('发送图片失败，请重试');}}pump();}
  function startFoodScan(file){if(!file||foodScanBusy)return;if(!/^image\//.test(file.type||'')){foodToast('请选择图片文件');return;}if(file.size>12*1024*1024){foodToast('图片太大，请选择 12MB 以内的图片');return;}foodScanBusy=true;foodScanItems=[];renderFoodScanResults();foodScanSetStatus('正在准备图片…',true);$('foodScanBtn').disabled=true;var scheme=(location.protocol==='https:')?'wss:':'ws:',rid='fs-'+String(Date.now())+'-'+Math.floor(Math.random()*10000);foodScanPrepare(file,function(buf,mime,compressed){try{foodScanSocket=new WebSocket(scheme+'//'+location.host+'/kitchen/ws');foodScanSocket.binaryType='arraybuffer';}catch(e){foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanSetStatus('无法建立识别连接',false);return;}var finished=false,uploading=true;foodScanSocket.onopen=function(){try{foodScanSocket.send(JSON.stringify({type:'kitchen.hello',protocol:kitchenProtocol,device_id:deviceId,capabilities:{touch:true,food_scan:true}}));foodScanSocket.send(JSON.stringify({type:'kitchen.food.scan.start',request_id:rid,source:foodSource,mime:mime,total_bytes:buf.byteLength,chunked:true,compressed:!!compressed}));foodScanSendChunks(foodScanSocket,buf,rid,function(){uploading=false;},function(msg){uploading=false;foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanSetStatus(msg||'发送图片失败，请重试',false);try{foodScanSocket.close();}catch(e){}});}catch(e){uploading=false;foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanSetStatus('发送图片失败，请重试',false);}};foodScanSocket.onmessage=function(ev){try{var m=JSON.parse(ev.data);if(m.type==='kitchen.food.scan.state'&&!uploading)foodScanSetStatus(m.message||'正在识别…',true);if(m.type==='kitchen.food.scan.result'){finished=true;foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanItems=m.items||[];renderFoodScanResults();var emptyMsg=(m.note==='VISION_UNAVAILABLE')?'图片已上传，但当前视觉模型不可用；订单截图会优先尝试本机文字识别，请查看 Gateway 的 FOOD-OCR 日志':'没有识别到明确食材，可以换一张更清楚的图';foodScanSetStatus(foodScanItems.length?('识别到 '+foodScanItems.length+' 项，请确认后入库'):emptyMsg,false);try{foodScanSocket.close();}catch(e){}}if(m.type==='kitchen.food.scan.error'){finished=true;foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanSetStatus('识别失败：'+(m.message||'请重试'),false);try{foodScanSocket.close();}catch(e){}}}catch(e){}};foodScanSocket.onerror=function(){if(!finished&&!uploading)foodScanSetStatus('识别连接出现问题，请重试',false);};foodScanSocket.onclose=function(){foodScanSocket=null;if(!finished){foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanSetStatus('识别连接中断，请重试',false);}};},function(){foodScanBusy=false;$('foodScanBtn').disabled=false;foodScanSetStatus('图片读取失败，请重试',false);});}
  function commitFoodScan(){if(!foodScanItems.length)return;var rows=foodScanItems.slice(),index=0,saved=0,failed=[];$('foodScanCommit').disabled=true;foodScanSetStatus('正在写入库存…',true);function next(){if(index>=rows.length){$('foodScanCommit').disabled=false;foodScanBusy=false;if(failed.length){foodScanSetStatus('已入库 '+saved+' 项，'+failed.length+' 项失败：'+failed.join('、'),false);}else{foodScanSetStatus('已完成入库 '+saved+' 项',false);foodToast('已入库 '+saved+' 项食材');foodScanItems=[];renderFoodScanResults();}loadFood();return;}var item=rows[index++],name=String(item.name||'').trim();if(!name){failed.push('空名称');next();return;}if(item.mode==='status'||item.unit==='状态'){foodAction('status',{name:name,status:item.status||'充足',category:item.category||'佐料/粮油'},function(err){if(err)failed.push(name);else saved++;next();});}else{foodAction('add',{name:name,amount:item.amount||1,unit:item.unit||'份',category:item.category||'其他',source:foodSource},function(err){if(err)failed.push(name);else saved++;next();});}}next();}
  function renderFood(){renderFoodHome();renderFoodInventory();renderFoodConsume();renderFoodLog();}
  function renderFoodHome(){var items=foodState.items||[],recommended=foodState.recommended||[],att=foodState.needs_attention||[],host,i;
    var meatTypes=0,meatPortions=0,vegTypes=0,eggCount=0,packaged=0,needCount=att.length;for(i=0;i<items.length;i++){var it=items[i];if(it.unit==='状态')continue;if(it.category==='肉类'||it.category==='海鲜'){meatTypes++;if(it.unit==='份')meatPortions+=Number(it.quantity||0);}if(it.category==='蔬菜')vegTypes++;if(it.category==='蛋类'&&it.unit==='个')eggCount+=Number(it.quantity||0);if(it.category==='奶制品'||it.category==='包装食品')packaged++;}
    var stats=[['肉 / 海鲜',meatPortions?String(meatPortions)+'份':String(meatTypes)+'种'],['蔬菜',String(vegTypes)+'种'],['鸡蛋',String(eggCount)+'个'],['待补状态',String(needCount)+'项']];host=$('foodSummary');host.innerHTML='';for(i=0;i<stats.length;i++){var st=el('div','food-stat');st.appendChild(el('span','',stats[i][0]));st.appendChild(el('strong','',stats[i][1]));host.appendChild(st);}
    host=$('foodRecommendedList');host.innerHTML='';if(!recommended.length){foodEmpty(host,'目前还没有可排序的食材。');}else{for(i=0;i<Math.min(recommended.length,10);i++){var item=recommended[i],r=el('div','food-row food-ranked-row'),rank=el('span','food-rank',String(i+1)),m=el('div','food-row-main');m.appendChild(el('div','food-row-name',item.name));m.appendChild(el('div','food-row-meta',foodUnitText(item)+' · '+(item.category||'食材')));r.appendChild(rank);r.appendChild(m);var tag=item.priority_window||'暂不着急';r.appendChild(el('div','food-row-value food-recommend-tag',tag));host.appendChild(r);}}
    renderModernFoodBits();
  }
  function renderFoodLog(){var host=$('foodLogList');if(!host)return;var events=foodState.events||[],dateEl=$('foodLogDate'),nameEl=$('foodLogName'),date=(dateEl?dateEl.value:'').trim(),q=(nameEl?nameEl.value:'').trim().toLowerCase(),shown=[];for(var i=0;i<events.length;i++){var ev=events[i],created=String(ev.created_at||''),name=String(ev.name||'');if(date&&created.slice(0,10)!==date)continue;if(q&&name.toLowerCase().indexOf(q)<0)continue;shown.push(ev);}var count=$('foodLogCount');if(count)count.textContent='共 '+shown.length+' 条记录';host.innerHTML='';if(!shown.length){foodEmpty(host,'没有符合当前筛选条件的记录。');return;}for(var j=0;j<shown.length;j++){var e=shown[j],label=e.event_type==='add'?'买入':(e.event_type==='consume'?'消耗':(e.event_type==='status'?'状态':(e.event_type==='rename'?'改名':'调整'))),val='';if(e.event_type==='add')val='+'+e.amount+' '+e.unit;if(e.event_type==='consume')val='−'+e.amount+' '+e.unit;if(e.event_type==='status'||e.event_type==='priority'||e.event_type==='rename'||e.event_type==='adjust')val=e.note||'';var row=el('div','food-row'),main=el('div','food-row-main');main.appendChild(el('div','food-row-name food-event-'+e.event_type,label+' · '+e.name));main.appendChild(el('div','food-row-meta',String(e.created_at||'').replace('T',' ').slice(0,16)+(e.source?' · '+e.source:'')));row.appendChild(main);row.appendChild(el('div','food-row-value',val));host.appendChild(row);}}
  var foodEditItem=null;
  function closeFoodEdit(){foodEditItem=null;if($('foodEditModal'))$('foodEditModal').className='modal food-edit-modal';}
  function openFoodEdit(item,focusQty){foodEditItem=item;if(!$('foodEditModal'))return;$('foodEditName').value=String(item.name||'');var qtyRow=$('foodEditQuantityRow'),qty=$('foodEditQuantity'),unit=$('foodEditUnit'),note=$('foodEditNote');if(item.unit==='状态'){qtyRow.style.display='none';note.textContent='这类食材使用“充足 / 一般 / 快没了”状态管理，这里只修改名称。';}else{qtyRow.style.display='grid';qty.value=String(item.quantity==null?0:item.quantity);qty.step=item.unit==='份'?'0.5':'1';unit.textContent=item.unit||'';note.textContent='数量修改属于库存盘点，不会记成烹饪消耗。坏掉、丢弃或登记错误，都可以直接改成实际剩余数量。';}$('foodEditModal').className='modal food-edit-modal show';setTimeout(function(){try{(focusQty&&item.unit!=='状态'?qty:$('foodEditName')).focus();}catch(e){}},80);}
  function saveFoodEdit(){if(!foodEditItem)return;var oldName=String(foodEditItem.name||''),newName=String($('foodEditName').value||'').trim();if(!newName){foodToast('名称不能为空');return;}var params={name:oldName,new_name:newName};if(foodEditItem.id!==undefined&&foodEditItem.id!==null)params.item_id=foodEditItem.id;if(foodEditItem.unit!=='状态'){var q=parseFloat($('foodEditQuantity').value);if(!isFinite(q)||q<0){foodToast('请输入正确的库存数量');return;}params.quantity=q;}var btn=$('foodEditSave'),note=$('foodEditNote'),oldText=btn.textContent;btn.disabled=true;btn.textContent='保存中…';if(note)note.textContent='正在写入库存并校验…';foodAction('edit',params,function(err,data){btn.disabled=false;btn.textContent=oldText;if(err){if(note)note.textContent='保存失败：'+err.message;return;}var edited=data&&data.edited;if(!edited||String(edited.name||'')!==newName){if(note)note.textContent='保存校验失败：Gateway 返回的数据没有发生变化，请重试。';return;}foodEditItem=edited;closeFoodEdit();foodToast('已保存：'+newName);foodInventoryPage=1;loadFood();});}
  if($('foodEditCancel'))$('foodEditCancel').onclick=closeFoodEdit;if($('foodEditSave'))$('foodEditSave').onclick=saveFoodEdit;if($('foodEditModal'))$('foodEditModal').onclick=function(ev){if(ev.target===this)closeFoodEdit();};
  function foodInventoryPriorityRank(v){var m={'这两天':0,'本周':1,'暂不着急':2};return Object.prototype.hasOwnProperty.call(m,v)?m[v]:3;}
  function foodInventoryUpdatedText(v){var t=String(v||'').replace('T',' ');return t?t.slice(5,16):'—';}
  function renderFoodInventory(){
    var all=(foodState.items||[]).slice(),host=$('foodInventoryList');if(!host)return;
    var searchEl=$('foodInventorySearch'),catEl=$('foodInventoryCategory'),dateEl=$('foodInventoryIntakeDate'),sortEl=$('foodInventorySort'),sizeEl=$('foodInventoryPageSize');
    var q=(searchEl?searchEl.value:'').trim().toLowerCase(),cat=(catEl?catEl.value:''),intakeDate=(dateEl?dateEl.value:''),sort=(sortEl?sortEl.value:'priority');
    foodInventoryPageSize=Math.max(5,parseInt(sizeEl?sizeEl.value:'10',10)||10);
    var items=all.filter(function(it){if(cat&&String(it.category||'')!==cat)return false;if(intakeDate&&String(it.last_added_at||'').slice(0,10)!==intakeDate)return false;if(!q)return true;var hay=[it.name,it.category,it.source,it.storage,it.status,it.priority_window,it.last_added_at].join(' ').toLowerCase();return hay.indexOf(q)>=0;});
    items.sort(function(a,b){
      if(sort==='intake')return String(b.last_added_at||'').localeCompare(String(a.last_added_at||''))||String(a.name||'').localeCompare(String(b.name||''));
      if(sort==='updated')return String(b.updated_at||'').localeCompare(String(a.updated_at||''));
      if(sort==='category')return (String(a.category||'').localeCompare(String(b.category||''))||String(a.name||'').localeCompare(String(b.name||'')));
      if(sort==='name')return String(a.name||'').localeCompare(String(b.name||''));
      var ra=a.unit==='状态'?4:foodInventoryPriorityRank(String(a.priority_window||'暂不着急')),rb=b.unit==='状态'?4:foodInventoryPriorityRank(String(b.priority_window||'暂不着急'));
      return ra-rb||String(a.updated_at||'').localeCompare(String(b.updated_at||''))||String(a.name||'').localeCompare(String(b.name||''));
    });
    var pages=Math.max(1,Math.ceil(items.length/foodInventoryPageSize));foodInventoryPage=Math.min(Math.max(1,foodInventoryPage),pages);
    var start=(foodInventoryPage-1)*foodInventoryPageSize,end=Math.min(items.length,start+foodInventoryPageSize),pageItems=items.slice(start,end);
    if($('foodInventoryCount'))$('foodInventoryCount').textContent=items.length?('显示 '+(start+1)+'–'+end+' / 共 '+items.length+' 项'):'共 0 项';
    if($('foodInventoryPageInfo'))$('foodInventoryPageInfo').textContent='第 '+foodInventoryPage+' / '+pages+' 页';
    if($('foodInventoryPrev'))$('foodInventoryPrev').disabled=foodInventoryPage<=1;
    if($('foodInventoryNext'))$('foodInventoryNext').disabled=foodInventoryPage>=pages;
    host.innerHTML='';if(!pageItems.length){foodEmpty(host,q||cat||intakeDate?'没有符合查询条件的食材。':'还没有食材。去“买入食材”加入第一项。');return;}
    for(var i=0;i<pageItems.length;i++){(function(item){
      var row=el('div','food-inventory-row'),main=el('div','food-inventory-main'),nameLine=el('div','food-inventory-name-line');nameLine.appendChild(el('strong','',item.name));var renameBtn=el('button','food-inventory-rename','编辑');renameBtn.type='button';renameBtn.onclick=function(){openFoodEdit(item,false);};nameLine.appendChild(renameBtn);main.appendChild(nameLine);
      var meta=[item.category||'食材'];if(item.source)meta.push('来源 '+item.source);if(item.storage)meta.push(item.storage);if(item.last_added_at)meta.push('入库 '+foodInventoryUpdatedText(item.last_added_at));main.appendChild(el('small','',meta.join(' · ')));row.appendChild(main);
      row.appendChild(el('div','food-inventory-qty',foodUnitText(item)));
      var state=el('div','food-inventory-state');if(item.unit==='状态'){state.appendChild(el('span','food-inventory-badge status-'+(item.status==='快没了'?'low':(item.status==='一般'?'mid':'ok')),item.status||'一般'));}else{
        var sel=document.createElement('select');sel.className='food-select food-inventory-priority';var opts=['这两天','本周','暂不着急'];for(var j=0;j<opts.length;j++){var o=document.createElement('option');o.value=opts[j];o.textContent=opts[j];if(opts[j]===item.priority_window)o.selected=true;sel.appendChild(o);}sel.onchange=function(){foodAction('priority',{name:item.name,priority:this.value},function(err){if(!err)foodToast(item.name+'：已调整推荐顺序');});};state.appendChild(sel);
      }row.appendChild(state);row.appendChild(el('div','food-inventory-updated',foodInventoryUpdatedText(item.updated_at)));host.appendChild(row);
    })(pageItems[i]);}
  }
  function renderFoodConsume(){var sel=$('foodConsumeName'),items=foodState.items||[],old=sel.value,i;sel.innerHTML='';for(i=0;i<items.length;i++){var it=items[i];if(it.unit==='状态')continue;var o=document.createElement('option');o.value=it.name;o.textContent=it.name+' · '+foodUnitText(it);sel.appendChild(o);}if(old)sel.value=old;var has=sel.options.length>0;$('foodConsumeAmount').disabled=!has;var submit=$('foodConsumeForm').querySelector('button[type="submit"]');if(submit)submit.disabled=!has;if(!has){var o2=document.createElement('option');o2.textContent='暂无可消耗食材';o2.value='';sel.appendChild(o2);sel.disabled=true;}else sel.disabled=false;}
  if($('foodShowAllBtn'))$('foodShowAllBtn').onclick=function(){foodSwitch('inventory');};
  if($('foodInventorySearch'))$('foodInventorySearch').oninput=function(){foodInventoryPage=1;renderFoodInventory();};
  if($('foodInventoryCategory'))$('foodInventoryCategory').onchange=function(){foodInventoryPage=1;renderFoodInventory();};
  if($('foodInventoryIntakeDate'))$('foodInventoryIntakeDate').onchange=function(){foodInventoryPage=1;renderFoodInventory();};
  if($('foodInventorySort'))$('foodInventorySort').onchange=function(){foodInventoryPage=1;renderFoodInventory();};
  if($('foodInventoryPageSize'))$('foodInventoryPageSize').onchange=function(){foodInventoryPage=1;renderFoodInventory();};
  if($('foodInventoryPrev'))$('foodInventoryPrev').onclick=function(){if(foodInventoryPage>1){foodInventoryPage--;renderFoodInventory();}};
  if($('foodInventoryNext'))$('foodInventoryNext').onclick=function(){foodInventoryPage++;renderFoodInventory();};
  if($('foodLogBtn'))$('foodLogBtn').onclick=function(){foodSwitch('log');};
  if($('foodLogBack'))$('foodLogBack').onclick=function(){foodSwitch('home');};
  if($('foodLogDate'))$('foodLogDate').onchange=renderFoodLog;
  if($('foodLogName'))$('foodLogName').oninput=renderFoodLog;
  if($('foodLogClear'))$('foodLogClear').onclick=function(){$('foodLogDate').value='';$('foodLogName').value='';renderFoodLog();};
  (function(){var buttons=document.querySelectorAll('.food-channel'),i;for(i=0;i<buttons.length;i++)buttons[i].onclick=function(){var j;foodSource=this.getAttribute('data-source')||'菜市场';for(j=0;j<buttons.length;j++)buttons[j].className='food-channel'+(buttons[j]===this?' active':'');var hints={菜市场:'菜市场：拍食材最方便；数量不准时在识别结果里改一下。',网上APP:'网上APP：优先直接选择订单截图，通常识别最完整。',超市:'超市：优先拍小票，也可以直接拍买回来的商品。'};$('foodChannelHint').textContent=hints[foodSource]||'';};})();
  $('foodScanBtn').onclick=function(){$('foodScanFile').click();};$('foodScanFile').onchange=function(){var f=this.files&&this.files[0];this.value='';if(f)startFoodScan(f);};$('foodScanAddMissing').onclick=foodScanAddMissing;$('foodScanClear').onclick=foodScanReset;$('foodScanCommit').onclick=commitFoodScan;
  $('foodAddCategory').onchange=function(){var c=this.value,u=$('foodAddUnit');if(c==='肉类'||c==='海鲜'||c==='蔬菜')u.value='份';else if(c==='蛋类')u.value='个';else if(c==='奶制品'||c==='包装食品')u.value='盒';else if(c==='主食')u.value='包';};
  $('foodAddForm').onsubmit=function(ev){ev.preventDefault();var name=$('foodAddName').value.trim(),amount=parseFloat($('foodAddAmount').value||'0');if(!name||!(amount>0)){foodToast('先填写食材名称和数量');return;}foodAction('add',{name:name,amount:amount,unit:$('foodAddUnit').value,category:$('foodAddCategory').value,source:foodSource},function(err){if(!err){foodToast(name+' 已加入');$('foodAddName').value='';$('foodAddAmount').value='1';foodSwitch('home');}});};
  $('foodConsumeForm').onsubmit=function(ev){ev.preventDefault();var name=$('foodConsumeName').value,amount=parseFloat($('foodConsumeAmount').value||'0');if(!name||!(amount>0)){foodToast('请选择食材并填写用量');return;}foodAction('consume',{name:name,amount:amount},function(err){if(!err){foodToast('已记录 '+name+' 的消耗');$('foodConsumeAmount').value='1';foodSwitch('home');}});};
  $('foodStatusForm').onsubmit=function(ev){ev.preventDefault();var name=$('foodStatusName').value.trim();if(!name){foodToast('先填写佐料或粮油名称');return;}foodAction('status',{name:name,status:$('foodStatusValue').value,category:'佐料/粮油'},function(err){if(!err){foodToast(name+'：'+$('foodStatusValue').value);$('foodStatusName').value='';}});};

  function setHomeMode(on,pageKind){
    var p=$('mainPanel');if(p)p.className='panel'+(on?' home-mode':'');
    if(pageKind)document.body.setAttribute('data-k-page',pageKind);
    if(on){document.body.classList.add('home-active');}else{document.body.classList.remove('home-active');if($('qaDock'))$('qaDock').classList.remove('qa-active');}
  }
  function homeFoodSummaryText(){var items=foodState.items||[],meat=0,veg=0,eggs=0,att=(foodState.needs_attention||[]).length;for(var i=0;i<items.length;i++){var it=items[i];if(it.unit==='状态')continue;if((it.category==='肉类'||it.category==='海鲜')&&it.unit==='份')meat+=Number(it.quantity||0);if(it.category==='蔬菜')veg+=1;if(it.category==='蛋类'&&it.unit==='个')eggs+=Number(it.quantity||0);}var bits=[];if(meat)bits.push('肉/海鲜 '+meat+'份');if(veg)bits.push('蔬菜 '+veg+'种');if(eggs)bits.push('鸡蛋 '+eggs+'个');if(att)bits.push(att+'项需补');return bits.length?bits.join(' · '):'先从第一笔真实食材记录开始';}
  function renderModernFoodBits(){var stats=$('homeFoodStats'),list=$('homePriorityList'),more=$('homePriorityMore');if(stats)stats.textContent=homeFoodSummaryText();if(!list)return;list.innerHTML='';var arr=foodState.priority||[];if(!arr.length){list.appendChild(el('div','home-priority-empty','目前没有需要赶着吃的东西，挺好。'));}else{for(var i=0;i<Math.min(arr.length,4);i++){(function(it){var row=el('button','home-priority-item');row.type='button';var left=el('div','');left.appendChild(el('div','home-priority-name',it.name));left.appendChild(el('div','home-priority-meta',foodUnitText(it)+' · '+(it.category||'食材')));row.appendChild(left);var tag=el('span','home-priority-tag'+(it.priority_window==='本周'?' week':''),it.priority_window||'建议先用');row.appendChild(tag);row.onclick=function(){openFood('inventory');};list.appendChild(row);})(arr[i]);}}if(more)more.onclick=function(){openFood('home');};}
  function navActiveForView(v){if(!v||!v.type)return 'home';if(v.type==='kitchen.show_idle')return 'home';if(v.type==='kitchen.show_dashboard')return 'today';if(v.type==='kitchen.show_shopping')return 'shopping';if(v.type==='kitchen.show_menu'||v.type==='kitchen.show_picker'||v.type==='kitchen.show_prep'||v.type==='kitchen.show_recipe'||v.type==='kitchen.show_finish'||v.type==='kitchen.show_save_private'||v.type==='kitchen.show_timeline')return 'today';return 'home';}
  function updateGlobalNav(active){var nav=$('globalBottomNav');if(!nav)return;var bs=nav.querySelectorAll('button[data-global-nav]');for(var i=0;i<bs.length;i++)bs[i].classList.toggle('active',bs[i].getAttribute('data-global-nav')===active);}
  function globalNavigate(target){if($('foodModal')&&$('foodModal').classList.contains('show')){$('foodModal').className='food-modal';$('foodModal').setAttribute('aria-hidden','true');}if(target==='home')action('home');else if(target==='today')action('today');else if(target==='food')openFood('home');else if(target==='shopping')openShoppingModal();}
  function bottomNav(active){updateGlobalNav(active);return document.createDocumentFragment();}
  (function(){var nav=$('globalBottomNav');if(!nav)return;var bs=nav.querySelectorAll('button[data-global-nav]');for(var i=0;i<bs.length;i++){bs[i].onclick=function(){globalNavigate(this.getAttribute('data-global-nav'));};}})();
  function dishNames(v){var items=(v&&v.items&&v.items.length!==undefined)?v.items:[],names=[];for(var i=0;i<items.length;i++)names.push(typeof items[i]==='string'?items[i]:String((items[i]&&items[i].name)||('菜品 '+(i+1))));return names;}
  function renderModernHome(v){
    setHomeMode(true,'home');var host=$('content');host.innerHTML='';var dash=el('div','home-dashboard'),names=dishNames(v);
    var welcome=el('section','mock-welcome');
    var wcopy=el('div','mock-welcome-copy');wcopy.appendChild(el('div','mock-greeting',names.length?'晚饭准备好了吗？':'晚上好！'));wcopy.appendChild(el('div','mock-greeting-sub',names.length?'今天的菜单已经在这里，随时可以开始。':'今天的菜单还没定，先从选菜开始吧。'));
    var primary=el('button','mock-big-primary',names.length?'开工烧饭  →':'🍴 获取今日菜谱  →');primary.type='button';primary.onclick=function(){action(names.length?'dashboard':'home');};wcopy.appendChild(primary);
    if(!names.length)wcopy.appendChild(el('div','mock-primary-note','让小K为你准备一份合适的菜单'));
    welcome.appendChild(wcopy);
    dash.appendChild(welcome);

    if(names.length){var today=el('section','mock-home-today');var th=el('div','mock-section-head');var ttl=el('div','');ttl.appendChild(el('strong','','今日菜谱'));ttl.appendChild(el('small','',names.length+' 道菜'));th.appendChild(ttl);var enter=el('button','mock-text-link','查看全部  ›');enter.onclick=function(){action('today');};th.appendChild(enter);today.appendChild(th);var tl=el('div','mock-home-dishes');for(var i=0;i<Math.min(names.length,4);i++){var row=el('button','mock-home-dish');row.type='button';row.appendChild(el('span','mock-home-dish-num',String(i+1)));row.appendChild(el('strong','',names[i]));row.appendChild(el('span','mock-home-dish-arrow','›'));row.onclick=(function(n){return function(){action('recipe',{value:n});};})(names[i]);tl.appendChild(row);}today.appendChild(tl);dash.appendChild(today);}

    var cards=el('div','mock-feature-grid');
    function feature(icon,title,sub,fn){var b=el('button','mock-feature-card');b.type='button';b.innerHTML='<span class="mock-feature-icon">'+icon+'</span><strong>'+title+'</strong><small>'+sub+'</small>';b.onclick=fn;cards.appendChild(b);}
    var icoMenu='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v8M4 3v5a3 3 0 0 0 6 0V3M7 11v10M16 3v18M16 3c3 2 4 5 4 8h-4"/></svg>';
    var icoShop='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h2l2.2 10.2a2 2 0 0 0 2 1.6h7.9a2 2 0 0 0 2-1.6L21 8H7M10 21h.01M18 21h.01"/></svg>';
    var icoFood='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M5 10h14M9 6v1M9 13v2"/></svg>';
    var icoTimer='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 2h6M12 2v3"/></svg>';
    feature(icoMenu,'选择今日菜谱','从库存和私房菜中选择',function(){action('picker');});
    feature(icoShop,'今日采购清单','缺什么一目了然',openShoppingModal);
    feature(icoFood,'食材管理','查看和管理家中食材',function(){openFood('home');});
    feature(icoTimer,'厨房计时','让烹饪更从容',openStandaloneTimer);dash.appendChild(cards);
    dash.appendChild(bottomNav('home'));host.appendChild(dash);loadFood();
  }
  function timerForDishStep(dish,step){for(var i=0;i<latestTimers.length;i++){var t=latestTimers[i];if(t.kind!=='standalone'&&t.dish===dish&&Number(t.step)===Number(step))return t;}return null;}
  function dashboardTimerLabel(t){return t.kind==='standalone'?'厨房通用':((t.dish||'菜品')+' · 第'+(Number(t.step)+1)+'步');}
  function renderDashboardCurrentTimer(v){var host=$('r49CurrentTimerHost');if(!host)return;host.innerHTML='';var c=v.current||null;if(!c)return;var t=timerForDishStep(c.name,c.step),box=el('div','r49-main-timer'),copy=el('div','r49-main-timer-copy');
    if(t){copy.appendChild(el('small','',c.name+' · 本步骤计时'));var tm=el('strong',t.status==='finished'?'done':'',t.status==='finished'?'时间到':fmt(remaining(t)));tm.setAttribute('data-r49-timer-time',t.timer_id);copy.appendChild(tm);box.appendChild(copy);var b=el('button','',t.status==='running'?'暂停':(t.status==='paused'?'继续':'结束提醒'));b.onclick=function(){if(t.status==='running')action('timer_pause',{timer_id:t.timer_id});else if(t.status==='paused')action('timer_resume',{timer_id:t.timer_id});else action('timer_dismiss',{timer_id:t.timer_id});};box.appendChild(b);}
    else if(c.timer_hint&&Number(c.timer_hint.default_sec)>0){copy.appendChild(el('small','','本步骤建议计时'));copy.appendChild(el('strong','',fmt(Number(c.timer_hint.default_sec))));box.appendChild(copy);var st=el('button','','开始计时');st.onclick=function(){action('timer_start',{seconds:Number(c.timer_hint.default_sec)});};box.appendChild(st);}
    else{copy.appendChild(el('small','','本步骤'));copy.appendChild(el('strong','','无需计时'));box.appendChild(copy);var more=el('button','','厨房计时');more.onclick=openTimerCenter;box.appendChild(more);}host.appendChild(box);
  }
  function renderDashboardTimers(v){var host=$('r49DashboardTimerHost');if(!host)return;host.innerHTML='';var arr=latestTimers.slice(0);arr.sort(function(a,b){if(a.status==='finished'&&b.status!=='finished')return -1;if(b.status==='finished'&&a.status!=='finished')return 1;return Number(a.ends_at||9e18)-Number(b.ends_at||9e18);});var shown=0;for(var i=0;i<arr.length&&shown<3;i++){var t=arr[i];if(v.current&&t.kind!=='standalone'&&t.dish===v.current.name&&Number(t.step)===Number(v.current.step))continue;(function(tt){var b=el('button','r49-timer-mini '+tt.status);b.type='button';b.appendChild(el('span','dish',dashboardTimerLabel(tt)));var tx=el('strong','',tt.status==='finished'?'时间到':fmt(remaining(tt)));tx.setAttribute('data-r49-timer-time',tt.timer_id);b.appendChild(tx);b.onclick=function(){if(tt.kind==='standalone')openTimerCenter();else action('timer_open',{timer_id:tt.timer_id});};host.appendChild(b);})(t);shown++;}if(!shown)host.appendChild(el('div','r49-pending-empty','目前没有后台计时。'));renderDashboardCurrentTimer(v);}
  function renderDashboard(v){setHomeMode(true,'dashboard');var host=$('content');host.innerHTML='';var page=el('div','r49-dashboard');
    var hd=el('div','r49-dash-head'),hc=el('div','');hc.appendChild(el('h2','','厨房中台'));hc.appendChild(el('p','',v.message||'一屏掌控正在做的菜'));hd.appendChild(hc);var da=el('div','r50-dash-actions');da.appendChild(el('div','r49-dash-status','厨房运行正常'));var finishDay=el('button','r50-finish-day','✓ 结束今日厨房');finishDay.type='button';finishDay.onclick=function(){action('finish_start');};da.appendChild(finishDay);hd.appendChild(da);page.appendChild(hd);
    var body=el('div','r49-dash-body'),c=v.current||null,left=el('section','r49-card r49-current');left.appendChild(el('div','r49-label','● 当前烹饪'));
    if(c){var title=el('div','r49-current-title'),tg=el('div','');tg.appendChild(el('h3','',c.name));var pct=Math.max(0,Math.min(100,Math.round(((Number(c.step)||0)+1)*100/Math.max(1,Number(c.total_steps)||1)))),prog=el('div','r49-progress'),fill=el('i','');fill.style.width=pct+'%';prog.appendChild(fill);tg.appendChild(prog);title.appendChild(tg);title.appendChild(el('span','r49-step-count','第 '+((Number(c.step)||0)+1)+' / '+Math.max(1,Number(c.total_steps)||1)+' 步'));left.appendChild(title);left.appendChild(el('div','r49-step-copy',c.step_text||'准备开始'));var bottom=el('div','r49-current-bottom'),th=el('div','');th.id='r49CurrentTimerHost';bottom.appendChild(th);var cont=el('button','r49-continue','继续做这道菜  ›');cont.onclick=function(){action('recipe',{value:c.name});};bottom.appendChild(cont);left.appendChild(bottom);}else{left.appendChild(el('div','r49-step-copy','今天还没有菜谱。'));var go=el('button','r49-continue','选择今日菜谱');go.onclick=function(){action('today');};left.appendChild(go);}body.appendChild(left);
    var side=el('div','r49-side'),others=el('section','r49-card r49-side-card');var allItems=(v.items&&v.items.length!==undefined)?v.items:[];var otherItems=[];for(var oi=0;oi<allItems.length;oi++){var oitem=allItems[oi]||{},oname=String(oitem.name||'');if(oname&&(!c||oname!==c.name))otherItems.push(oitem);}others.appendChild(el('h4','','其他菜'+(otherItems.length?' · '+otherItems.length:'')));var ol=el('div','r49-other-list');if(!otherItems.length){ol.appendChild(el('div','r49-other-empty','今天没有其他待烧的菜。'));}else{for(var oi2=0;oi2<Math.min(otherItems.length,4);oi2++){(function(item){var label=String(item.name||''),started=!!item.has_progress&&Number(item.progress_step||0)>0,row=el('button','r49-other-row'+(started?' started':''));row.type='button';row.appendChild(el('strong','',label));row.appendChild(el('span','',started?'已开始':'待开始'));row.onclick=function(){action('recipe',{value:label});};ol.appendChild(row);})(otherItems[oi2]);}}others.appendChild(ol);side.appendChild(others);
    var timers=el('section','r49-card r49-side-card');var trh=el('div','');trh.style.display='flex';trh.style.justifyContent='space-between';trh.style.alignItems='center';trh.appendChild(el('h4','','后台计时'));var all=el('button','r49-timer-center-link','查看全部 ›');all.onclick=openTimerCenter;trh.appendChild(all);timers.appendChild(trh);var tlh=el('div','r49-timer-mini-list');tlh.id='r49DashboardTimerHost';timers.appendChild(tlh);side.appendChild(timers);
    var prep=el('section','r49-card r49-side-card');prep.appendChild(el('h4','','统一备菜'));var pe=el('div','r49-prep-entry'),pc=el('div','r49-prep-copy'),done=Number(v.prep_completed||0),ptotal=Number(v.prep_total||0),ppct=ptotal?Math.max(0,Math.min(100,Math.round(done*100/ptotal))):0;pc.appendChild(el('strong','',v.prep_all_done?'备菜已完成':(ptotal?done+' / '+ptotal+' 已完成':'查看今日备菜')));pc.appendChild(el('small','',v.prep_all_done?'需要复查时可以随时进入。':'开火前把清洗、切配、腌制等一次处理完。'));var pp=el('div','r49-prep-progress'),pi=el('i','');pi.style.width=(v.prep_all_done?100:ppct)+'%';pp.appendChild(pi);pc.appendChild(pp);pe.appendChild(pc);var pb=el('button','r49-prep-button',v.prep_all_done?'查看备菜':'进入备菜');pb.type='button';pb.onclick=function(){action('prep');};pe.appendChild(pb);prep.appendChild(pe);side.appendChild(prep);body.appendChild(side);page.appendChild(body);
    var qb=el('div','r49-quickbar');function q(icon,text,fn){var b=el('button','','');b.type='button';b.appendChild(el('span','ico',icon));b.appendChild(el('span','',text));b.onclick=fn;qb.appendChild(b);}q('🛒','今日采购',openShoppingModal);q('◷','厨房计时',openTimerCenter);page.appendChild(qb);host.appendChild(page);renderDashboardTimers(v);updateGlobalNav('today');}
  function renderTodayMenu(v){
    setHomeMode(true,'today');var host=$('content');host.innerHTML='';var page=el('div','mock-page today-page'),names=dishNames(v),items=v.items||[];
    var head=el('div','mock-page-head');var hc=el('div','');hc.appendChild(el('h2','','今日菜谱'));hc.appendChild(el('p','',names.length?(names.length+' 道菜 · 今天就按这个吃'):'今天还没有选菜'));head.appendChild(hc);page.appendChild(head);
    var list=el('div','mock-menu-list');if(!names.length){var empty=el('section','mock-empty');empty.appendChild(el('strong','','今天还没有菜谱'));empty.appendChild(el('span','','先去选择今天想吃的菜吧。'));var eb=el('button','mock-big-primary','获取今日菜谱 →');eb.onclick=function(){action('today');};empty.appendChild(eb);list.appendChild(empty);}else{for(var i=0;i<items.length;i++){(function(item,index){var label=typeof item==='string'?item:String((item&&item.name)||('菜品 '+(index+1))),row=el('div','mock-menu-row');row.appendChild(el('span','mock-menu-index',index+1));var mid=el('button','mock-menu-copy mock-menu-open');mid.type='button';mid.appendChild(el('strong','',label));var meta='等待统一备菜';if(item&&item.has_progress&&Number(item.total_steps)>0){var ps=Number(item.progress_step)||0;meta=ps<=0?'等待统一备菜':'烹饪进度 '+ps+' / '+Math.max(1,Number(item.total_steps)-1);}mid.appendChild(el('small','',meta));mid.onclick=function(){action('recipe',{value:label});};row.appendChild(mid);var acts=el('span','mock-menu-actions');var open=el('button','mock-menu-arrow','›');open.type='button';open.onclick=function(){action('recipe',{value:label});};acts.appendChild(open);var rm=el('button','mock-menu-remove','移除');rm.type='button';rm.onclick=function(ev){if(ev&&ev.stopPropagation)ev.stopPropagation();openTodayRemoveModal(label);};acts.appendChild(rm);row.appendChild(acts);list.appendChild(row);})(items[i],i);}}page.appendChild(list);
    var add=el('button','mock-soft-action','＋ 继续添加菜品');add.onclick=function(){action('picker');};page.appendChild(add);
    if(names.length){var prep=el('button','mock-bottom-primary','▶  开始统一备菜');prep.onclick=function(){action('prep');};page.appendChild(prep);var util=el('div','mock-inline-actions mock-inline-actions-single');var finish=el('button','','结束今日厨房');finish.onclick=function(){action('finish_start');};util.appendChild(finish);page.appendChild(util);}page.appendChild(bottomNav('today'));host.appendChild(page);
  }
  function renderPrep(v){
    setHomeMode(true,'prep');var host=$('content');host.innerHTML='';var page=el('div','mock-page prep-page'),groups=v.groups||[],done=Number(v.completed||0),total=Number(v.total||0);
    var head=el('div','mock-cook-head'),hc=el('div','');hc.appendChild(el('h2','','统一备菜'));hc.appendChild(el('p','',v.message||'把今天所有菜的准备工作一次完成'));head.appendChild(hc);var backDash=el('button','mock-cook-menu-back','厨房中台');backDash.type='button';backDash.setAttribute('aria-label','返回厨房中台');backDash.title='返回厨房中台';backDash.onclick=function(){action('dashboard');};head.appendChild(backDash);page.appendChild(head);
    var prog=el('section','mock-prep-progress');var top=el('div','');top.appendChild(el('strong','',v.all_done?'全部完成':'备菜进度'));top.appendChild(el('span','',done+' / '+total));prog.appendChild(top);var bar=el('div','mock-progress-bar'),fill=el('i','');fill.style.width=(total?Math.round(done*100/total):0)+'%';bar.appendChild(fill);prog.appendChild(bar);page.appendChild(prog);
    var wrap=el('div','mock-prep-groups');for(var i=0;i<groups.length;i++){(function(g){var card=el('section','mock-prep-card'+(g.done?' done':''));var hd=el('div','mock-prep-head');hd.appendChild(el('strong','',g.dish||'菜品'));hd.appendChild(el('span','',g.done?'已完成':'待完成'));card.appendChild(hd);var refs=[];if((g.ingredients||[]).length)refs.push('食材：'+g.ingredients.join('、'));if((g.seasoning||[]).length)refs.push('调味：'+g.seasoning.join('、'));if(refs.length)card.appendChild(el('div','mock-prep-ref',refs.join('\n')));var tasks=el('div','mock-prep-tasks'),ts=g.tasks||[];for(var j=0;j<ts.length;j++){(function(t){var lab=el('label','mock-check-row'+(t.done?' checked':'')),cb=document.createElement('input');cb.type='checkbox';cb.checked=!!t.done;cb.onchange=function(){cb.disabled=true;action('prep_toggle',{value:t.id,done:cb.checked?'1':'0'},function(){cb.disabled=false;});};lab.appendChild(cb);lab.appendChild(el('span','',t.text));tasks.appendChild(lab);})(ts[j]);}card.appendChild(tasks);wrap.appendChild(card);})(groups[i]);}page.appendChild(wrap);if(v.all_done)page.appendChild(el('div','mock-success','✓ 所有备菜都完成了，可以开始正式烹饪。'));page.appendChild(bottomNav('today'));host.appendChild(page);
  }
  function renderPicker(v){
    setHomeMode(true,'picker');var host=$('content');host.innerHTML='';var page=el('div','mock-page picker-page');var head=el('div','mock-page-head'),hc=el('div','');hc.appendChild(el('h2','','选择今日菜谱'));hc.appendChild(el('p','','从库存或私房菜中挑选，组成今天的菜单'));head.appendChild(hc);page.appendChild(head);
    var active=String(v.active_source||'inventory'),today=v.today_names||[],sources=el('div','mock-source-grid'),body=el('div','mock-picker-body');
    function sourceCard(key,icon,title,sub){var b=el('button','mock-source-card'+(active===key?' active':''));b.type='button';b.innerHTML='<span class="mock-source-icon">'+icon+'</span><span class="mock-source-copy"><strong>'+title+'</strong><small>'+sub+'</small></span><span class="mock-source-arrow">→</span>';b.onclick=function(){active=key;drawSources();drawBody();};return b;}
    function drawSources(){sources.innerHTML='';sources.appendChild(sourceCard('inventory','▰','库存推荐菜','先选择家里已有的食材，再让小K推荐'));sources.appendChild(sourceCard('private','♥','私房菜','直接读取 Obsidian 私房菜目录'));}
    function recipeList(arr,emptyText){var wrap=el('div','mock-recipe-section');var search=document.createElement('input');search.className='mock-search';search.placeholder='搜索菜名…';var list=el('div','mock-recipe-list');function drawRows(){var q=(search.value||'').trim().toLowerCase();list.innerHTML='';var shown=0;for(var i=0;i<arr.length;i++){var it=arr[i],name=String(it.name||'');if(q&&name.toLowerCase().indexOf(q)<0)continue;var row=el('div','mock-recipe-row'),mid=el('div','mock-recipe-copy');mid.appendChild(el('strong','',name));var bits=[];if(it.estimated_text)bits.push(it.estimated_text);if(it.recommendation_reason)bits.push(it.recommendation_reason);if((it.inventory_matches||[]).length)bits.push('可用：'+it.inventory_matches.join('、'));if(it.source_kind==='private')bits.push('私房菜');mid.appendChild(el('small','',bits.join(' · ')||'菜谱'));row.appendChild(mid);var already=today.indexOf(name)>=0,add=el('button','mock-add-recipe',already?'已加入':'+ 加入');add.disabled=already;add.onclick=(function(id){return function(){action('today_add',{value:id});};})(it.id);row.appendChild(add);list.appendChild(row);shown++;}if(!shown)list.appendChild(el('div','mock-empty-line',emptyText));}search.oninput=drawRows;wrap.appendChild(search);wrap.appendChild(list);drawRows();return wrap;}
    function inventoryStep(){var selected=v.inventory_selected||[],results=v.recommended||[];if(selected.length){body.appendChild(el('div','mock-selected-note','已选择：'+selected.join('、')));var back=el('button','mock-text-action','← 重新选择食材');back.onclick=function(){action('picker');};body.appendChild(back);body.appendChild(recipeList(results,'暂时没有匹配到合适菜谱，可以换一组食材。'));return;}var items=v.inventory_items||[];if(!items.length){body.appendChild(el('div','mock-empty','食材管理里还没有可用食材。'));return;}var title=el('div','mock-section-title');title.appendChild(el('strong','','选择已有食材'));title.appendChild(el('small','','勾选你今天想优先使用的食材'));body.appendChild(title);var grid=el('div','mock-inventory-list');for(var i=0;i<items.length;i++){var it=items[i],lab=el('label','mock-inventory-row'),ck=document.createElement('input');ck.type='checkbox';ck.value=it.name;ck.className='inventory-choice';lab.appendChild(ck);var name=el('span','mock-inventory-name');name.appendChild(el('strong','',it.name));var meta=[];if(it.category)meta.push(it.category);if(it.quantity!==undefined&&it.unit)meta.push(String(it.quantity)+' '+it.unit);name.appendChild(el('small','',meta.join(' · ')));lab.appendChild(name);lab.appendChild(el('span','mock-category-pill',it.category||'食材'));grid.appendChild(lab);}body.appendChild(grid);var go=el('button','mock-bottom-primary','根据所选食材推荐菜谱  →');go.onclick=function(){var picked=[],nodes=document.querySelectorAll('.inventory-choice:checked');for(var n=0;n<nodes.length;n++)picked.push(nodes[n].value);if(!picked.length){foodToast('请先选择至少一种食材');return;}go.disabled=true;go.textContent='正在推荐…';action('inventory_recommend',{value:JSON.stringify(picked)},function(err){if(err){go.disabled=false;go.textContent='根据所选食材推荐菜谱  →';foodToast('推荐失败，请稍后再试');}});};body.appendChild(go);}
    function drawBody(){body.innerHTML='';if(active==='private')body.appendChild(recipeList(v.private_recipes||[],(v.private_recipe_dirs||[]).length?'私房菜目录已找到，但没有解析到可用菜谱。':'没有找到 Obsidian 私房菜目录。'));else inventoryStep();}
    drawSources();page.appendChild(sources);page.appendChild(body);page.appendChild(bottomNav('today'));host.appendChild(page);drawBody();
  }
  function renderFinish(v){
    setHomeMode(true,'finish');var host=$('content');host.innerHTML='';var page=el('div','finish-page');
    var head=el('div','page-heading'),hc=el('div','');hc.appendChild(el('h2','','今日厨房收尾'));hc.appendChild(el('p','','今天的菜都做完后，再结束今天的厨房流程。'));head.appendChild(hc);page.appendChild(head);
    var summary=el('section','finish-summary');summary.appendChild(el('div','finish-summary-icon','✓'));summary.appendChild(el('h3','','准备结束今天的厨房？'));summary.appendChild(el('p','',v.message||'确认后先统一登记今天的食材消耗，再选择是否保存私房菜。'));page.appendChild(summary);
    var note=el('section','finish-note');note.appendChild(el('div','finish-note-icon','🍳'));var nt=el('div','');nt.appendChild(el('strong','','先做今日食材结算，再保存私房菜'));nt.appendChild(el('span','','今天所有菜的食材只在收尾时统一核对一次，不会在每道菜做完时打断你。'));note.appendChild(nt);page.appendChild(note);
    var acts=el('div','finish-modern-actions');acts.style.gridTemplateColumns='1fr';var yes=el('button','finish-modern-primary','进入今日食材结算');yes.onclick=function(){action('finish_confirm');};acts.appendChild(yes);page.appendChild(acts);page.appendChild(bottomNav('today'));host.appendChild(page);
  }
  function renderSavePrivate(v){
    setHomeMode(true,'finish');var host=$('content');host.innerHTML='';var page=el('div','finish-page private-save-page');
    var head=el('div','page-heading'),hc=el('div','');hc.appendChild(el('h2','','今天留下哪些菜？'));hc.appendChild(el('p','','满意的菜可以加入“私房菜”，以后直接从菜谱库再做。'));head.appendChild(hc);page.appendChild(head);
    var summary=el('section','finish-summary');summary.appendChild(el('div','finish-summary-icon','♡'));summary.appendChild(el('h3','','保存到私房菜'));summary.appendChild(el('p','',v.message||'只勾选你真正想留下的菜；不勾选也可以直接结束今天的厨房。'));page.appendChild(summary);
    var items=v.items||[],list=el('div','private-list');for(var i=0;i<items.length;i++){var row=el('label','private-row');var ck=document.createElement('input');ck.type='checkbox';ck.value=items[i];ck.className='private-choice';row.appendChild(ck);var txt=el('span','',items[i]);txt.appendChild(el('small','','加入私房菜'));row.appendChild(txt);list.appendChild(row);}page.appendChild(list);
    var acts=el('div','finish-modern-actions');var none=el('button','finish-modern-secondary','直接结束');none.onclick=function(){action('finish_no_save');};acts.appendChild(none);var save=el('button','finish-modern-primary','保存所选并结束');save.onclick=function(){var picked=[],nodes=document.querySelectorAll('.private-choice:checked');for(var n=0;n<nodes.length;n++)picked.push(nodes[n].value);action('finish_save',{value:JSON.stringify(picked)});};acts.appendChild(save);page.appendChild(acts);page.appendChild(bottomNav('today'));host.appendChild(page);
  }
  function renderCookingFlow(v){
    setHomeMode(true,'cooking');var host=$('content');host.innerHTML='';var page=el('div','mock-page cook-page'),stepNum=(parseInt(v.step,10)||0)+1,total=parseInt(v.total_steps,10)||1,isPrep=v.step_kind==='prep',isLast=stepNum>=total;
    var top=el('div','mock-cook-head');var tg=el('div','');tg.appendChild(el('h2','',v.title||'菜谱'));tg.appendChild(el('p','','第 '+stepNum+' / '+total+' 步'));top.appendChild(tg);var backMenu=el('button','mock-cook-menu-back','厨房中台');backMenu.type='button';backMenu.setAttribute('aria-label','返回厨房中台');backMenu.title='返回厨房中台';backMenu.onclick=function(){action('dashboard');};top.appendChild(backMenu);page.appendChild(top);
    var stage=el('div','mock-stage');stage.appendChild(el('span',isPrep?'active':'','1 备菜'));stage.appendChild(el('span',!isPrep&&!isLast?'active':'','2 烹饪'));stage.appendChild(el('span',isLast?'active':'','3 完成'));page.appendChild(stage);
    var layout=el('div','mock-cook-layout');
    var prev=el('button','mock-side-nav prev','‹');prev.type='button';prev.setAttribute('aria-label','上一步');prev.title='上一步';prev.disabled=stepNum<=1;prev.onclick=function(){action('prev');};layout.appendChild(prev);
    var center=el('div','mock-cook-center');var card=el('section','mock-cook-card');card.appendChild(el('div','mock-step-kicker',isPrep?'当前步骤 · 备菜':'当前步骤'));card.appendChild(el('div','mock-step-text',v.step_text||'（本步骤内容为空）'));var timerHost=el('div','');timerHost.id='stepTimerHost';card.appendChild(timerHost);var tips=v.key_points||[];if(tips.length){var tip=el('div','mock-k-tip');tip.appendChild(el('strong','','● 小K贴士'));tip.appendChild(el('span','',tips.join('；')));card.appendChild(tip);}var qt=el('button','mock-cook-timer-button','⏱ 多计时器中心');qt.onclick=openTimerCenter;card.appendChild(qt);center.appendChild(card);layout.appendChild(center);
    var next=el('button','mock-side-nav next','›');next.type='button';next.setAttribute('aria-label','下一步');next.title='下一步';next.disabled=stepNum>=total;next.onclick=function(){action('next');};layout.appendChild(next);
    page.appendChild(layout);host.appendChild(page);renderStepTimer(v);page.appendChild(bottomNav('today'));
  }
  function renderConsumption(v){
    setHomeMode(true,'consumption');var host=$('content');host.innerHTML='';var page=el('div','mock-page consumption-page'),isDay=String(v.scope||'')==='day';
    var head=el('div','mock-page-head'),hc=el('div','');hc.appendChild(el('h2','',isDay?'今日食材结算':'登记消耗'));hc.appendChild(el('p','',isDay?'核对今天实际用掉的食材和数量，确认后一次扣减库存。':((v.dish||'这道菜')+' · 按实际使用量扣减库存')));head.appendChild(hc);if(isDay){var backDash=el('button','mock-cook-menu-back','厨房中台');backDash.type='button';backDash.onclick=function(){action('dashboard');};head.appendChild(backDash);}page.appendChild(head);
    if(isDay){var note=el('section','finish-note');note.appendChild(el('div','finish-note-icon','▰'));var nt=el('div','');nt.appendChild(el('strong','','按实际消耗核对'));nt.appendChild(el('span','','系统已把今日菜谱涉及到的现有库存合并到一起；用量不对时直接用 ＋ / − 调整，没用到的改为 0。'));note.appendChild(nt);page.appendChild(note);}
    var items=v.items||[];if(!items.length){var empty=el('section','mock-empty');empty.appendChild(el('strong','','没有匹配到可扣减的库存食材'));empty.appendChild(el('span','',isDay?'可以直接继续到私房菜收尾；也可以之后在食材管理里手动修正。':'可能是食材名称不同，或这些原料没有录入库存。'));page.appendChild(empty);}else{var list=el('div','consumption-list');for(var i=0;i<items.length;i++){(function(it){var row=el('section','consumption-row');var copy=el('div','consumption-copy');copy.appendChild(el('strong','',it.name));var detail=String(it.recipe_text||'');if((it.dishes||[]).length)detail=(it.dishes.join('、')+(detail?' · '+detail:''));copy.appendChild(el('small','',detail+' · 库存 '+String(it.available)+' '+String(it.unit||'')));row.appendChild(copy);var ctl=el('div','consumption-control');var minus=el('button','','−');minus.type='button';var input=document.createElement('input');input.type='number';input.className='consumption-amount';input.setAttribute('data-name',it.name);input.min='0';input.max=String(it.available);input.step=(it.unit==='份'?'0.5':'1');input.value=String(it.suggested||0);var plus=el('button','','＋');plus.type='button';function clamp(n){var step=parseFloat(input.step)||1,max=parseFloat(input.max)||9999;n=Math.max(0,Math.min(max,n));return Math.round(n/step)*step;}minus.onclick=function(){input.value=String(clamp((parseFloat(input.value)||0)-(parseFloat(input.step)||1)));};plus.onclick=function(){input.value=String(clamp((parseFloat(input.value)||0)+(parseFloat(input.step)||1)));};ctl.appendChild(minus);ctl.appendChild(input);ctl.appendChild(el('span','consumption-unit',it.unit||''));ctl.appendChild(plus);row.appendChild(ctl);list.appendChild(row);})(items[i]);}page.appendChild(list);}
    var actions=el('div','consumption-actions');var skip=el('button','mock-soft-action',isDay?'暂不结算':'暂不扣库存');skip.type='button';skip.onclick=function(){if(isDay)action('day_consume_skip');else action('menu');};actions.appendChild(skip);var save=el('button','mock-bottom-primary',isDay?'确认食材消耗并继续':'确认消耗并返回今日菜谱');save.type='button';save.onclick=function(){var rows=document.querySelectorAll('.consumption-amount'),vals=[];for(var j=0;j<rows.length;j++){var a=Math.max(0,parseFloat(rows[j].value)||0);if(a>0)vals.push({name:rows[j].getAttribute('data-name'),amount:a});}save.disabled=true;save.textContent=isDay?'正在结算食材消耗…':'正在登记…';action(isDay?'day_consume_commit':'recipe_consume_commit',{value:JSON.stringify(vals)},function(err){if(err){save.disabled=false;save.textContent=isDay?'确认食材消耗并继续':'确认消耗并返回今日菜谱';}});};actions.appendChild(save);page.appendChild(actions);page.appendChild(bottomNav('today'));host.appendChild(page);
  }
  function renderShopping(v){
    setHomeMode(true,'shopping');var host=$('content');host.innerHTML='';var page=el('div','mock-page utility-page');var head=el('div','mock-cook-head'),hc=el('div','');hc.appendChild(el('h2','','今日采购清单'));hc.appendChild(el('p','',v.message||'勾选后自动保存，离开页面也不会丢'));head.appendChild(hc);var homeBtn=el('button','mock-cook-menu-back','首页');homeBtn.type='button';homeBtn.onclick=function(){action('home');};head.appendChild(homeBtn);page.appendChild(head);var groups=v.groups||[];if(!groups.length){page.appendChild(el('div','mock-empty','今天暂时没有需要采购的内容。'));}else{for(var i=0;i<groups.length;i++){var card=el('section','mock-shopping-card');card.appendChild(el('strong','',groups[i].name||'建议购买'));var items=groups[i].items||[];var list=el('div','mock-shopping-list');for(var j=0;j<items.length;j++){(function(item){var text=(item&&typeof item==='object')?String(item.text||''):String(item||''),itemId=(item&&typeof item==='object')?String(item.id||''):'',inv=(item&&typeof item==='object')?item.inventory:null;var lab=el('label','mock-shopping-row'+((item&&item.checked)?' checked':'')),ck=document.createElement('input');ck.type='checkbox';ck.checked=!!(item&&item.checked);ck.onchange=function(){var wanted=ck.checked;ck.disabled=true;action('shopping_toggle',{value:itemId,done:wanted?'1':'0'},function(err){ck.disabled=false;if(err){ck.checked=!wanted;lab.classList.toggle('checked',ck.checked);return;}lab.classList.toggle('checked',wanted);});};lab.appendChild(ck);var copy=el('span','shopping-item-copy');copy.appendChild(el('span','shopping-item-text',text));lab.appendChild(copy);if(inv&&inv.label)lab.appendChild(el('span','shopping-stock-badge',String(inv.label)));list.appendChild(lab);})(items[j]);}card.appendChild(list);page.appendChild(card);}}page.appendChild(bottomNav('shopping'));host.appendChild(page);
  }
  function renderTimeline(v){
    setHomeMode(true,'timeline');var host=$('content');host.innerHTML='';var page=el('div','utility-page');var head=el('div','page-heading'),hc=el('div','');hc.appendChild(el('h2','','烧菜顺序'));hc.appendChild(el('p','',String(v.eyebrow||'').replace(' · ','  ·  ')));head.appendChild(hc);page.appendChild(head);var items=v.items||[];if(!items.length){page.appendChild(el('div','utility-empty','今天还没有可显示的烧菜顺序。'));}else{var card=el('section','utility-page-card'),ol=el('ol','');for(var i=0;i<items.length;i++)ol.appendChild(el('li','',items[i]));card.appendChild(ol);page.appendChild(card);}page.appendChild(bottomNav('today'));host.appendChild(page);
  }
  function qaStateLabel(st){var m={idle:'可以提问',listening:'正在听…',uploading:'正在发送…',recognizing:'正在识别…',thinking:'正在回答…',speaking:'正在生成语音…',done:'回答完成',error:'出现问题'};return m[st]||'问小K';}
  function qaRender(q){if(!q)return;var st=String(q.status||'idle'),updated=Number(q.updated_at||0),age=updated?((Date.now()/1000)-updated):0;var serverBusy=(st==='uploading'||st==='recognizing'||st==='thinking'||st==='speaking');if(serverBusy&&age>180){serverBusy=false;st='error';q.error='上一条问答状态已超时，已经自动解锁，可以重新提问。';qaBusy=false;}else{qaBusy=serverBusy;}if(!qaRecording){$('qaBtn').disabled=false;$('qaBtn').className='qa-btn'+(qaBusy?' busy':'');$('qaBtn').textContent=qaBusy?'处理中…':'🎙 问小K';}$('qaStatus').textContent=qaRecording?'正在听…再次点击结束':qaStateLabel(st);$('qaTranscript').textContent=q.transcript?('你：'+q.transcript):'';$('qaAnswer').textContent=q.answer?('小K：'+q.answer):(st==='error'?(q.error||'语音问答失败'):'');var qd=$('qaDock');if(qd){var active=qaRecording||qaBusy||st==='done'||st==='error'||!!q.answer||!!q.transcript;qd.className='qa-dock'+(active?' qa-active':'');}}
  function qaFloatConcat(parts){var total=0,i;for(i=0;i<parts.length;i++)total+=parts[i].length;var out=new Float32Array(total),p=0;for(i=0;i<parts.length;i++){out.set(parts[i],p);p+=parts[i].length;}return out;}
  function qaDownsample(input,inRate,outRate){if(!input||!input.length)return new Float32Array(0);if(inRate===outRate)return input;if(outRate>inRate)outRate=inRate;var ratio=inRate/outRate,newLen=Math.max(1,Math.round(input.length/ratio)),out=new Float32Array(newLen),offset=0;for(var i=0;i<newLen;i++){var next=Math.min(input.length,Math.round((i+1)*ratio)),sum=0,count=0;for(var j=offset;j<next;j++){sum+=input[j];count++;}out[i]=count?sum/count:0;offset=next;}return out;}
  function qaWav(samples,rate){var b=new ArrayBuffer(44+samples.length*2),v=new DataView(b);function ws(o,t){for(var i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i));}ws(0,'RIFF');v.setUint32(4,36+samples.length*2,true);ws(8,'WAVE');ws(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);ws(36,'data');v.setUint32(40,samples.length*2,true);var o=44;for(var i=0;i<samples.length;i++,o+=2){var x=Math.max(-1,Math.min(1,samples[i]));v.setInt16(o,x<0?x*32768:x*32767,true);}return b;}
  function qaStopTracks(stream){if(!stream)return;try{var tracks=stream.getTracks?stream.getTracks():[];for(var i=0;i<tracks.length;i++){try{tracks[i].stop();}catch(e){}}}catch(e){}}
  function qaCleanupCapture(){if(qaAutoStop){clearTimeout(qaAutoStop);qaAutoStop=null;}try{if(qaProcessor){qaProcessor.disconnect();qaProcessor.onaudioprocess=null;}}catch(e){}try{if(qaSource)qaSource.disconnect();}catch(e){}qaStopTracks(qaStream);qaStream=null;qaProcessor=null;qaSource=null;if(qaCtx){try{qaCtx.close();}catch(e){}}qaCtx=null;}
  function qaStart(){if(qaBusy){$('qaStatus').textContent='上一条问题还在处理中，请稍候…';return;}if(qaRecording){qaStop();return;}unlockAudio();var gum=navigator.mediaDevices&&navigator.mediaDevices.getUserMedia;if(!gum){qaRender({status:'error',error:'当前页面无法使用麦克风，请确认使用 HTTPS 地址。',updated_at:Date.now()/1000});return;}$('qaBtn').disabled=false;$('qaBtn').className='qa-btn busy';$('qaBtn').textContent='请求麦克风…';$('qaStatus').textContent='正在请求麦克风权限…';navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false}).then(function(stream){qaStream=stream;var C=window.AudioContext||window.webkitAudioContext;if(!C)throw new Error('AudioContext unavailable');qaCtx=new C();try{qaCtx.resume();}catch(e){}qaSampleRate=qaCtx.sampleRate||48000;qaSource=qaCtx.createMediaStreamSource(stream);qaProcessor=qaCtx.createScriptProcessor(4096,1,1);qaChunks=[];qaProcessor.onaudioprocess=function(ev){if(!qaRecording)return;var input=ev.inputBuffer.getChannelData(0);qaChunks.push(new Float32Array(input));};qaSource.connect(qaProcessor);qaProcessor.connect(qaCtx.destination);qaRecording=true;qaStartedAt=Date.now();$('qaBtn').disabled=false;$('qaBtn').className='qa-btn recording';$('qaBtn').textContent='⏹ 结束提问';$('qaStatus').textContent='正在听…再次点击结束';$('qaTranscript').textContent='';$('qaAnswer').textContent='';qaAutoStop=setTimeout(function(){if(qaRecording)qaStop();},qaMaxMs);}).catch(function(err){qaCleanupCapture();qaRecording=false;$('qaBtn').disabled=false;$('qaBtn').className='qa-btn';$('qaBtn').textContent='🎙 问小K';qaRender({status:'error',error:'无法取得麦克风：'+String(err&&err.message?err.message:err),updated_at:Date.now()/1000});});}
  function qaStop(){if(!qaRecording)return;qaRecording=false;var duration=(Date.now()-qaStartedAt)/1000,parts=qaChunks.slice(),rate=qaSampleRate||48000;qaCleanupCapture();$('qaBtn').className='qa-btn busy';$('qaBtn').disabled=true;$('qaBtn').textContent='处理中…';if(duration<0.35||!parts.length){qaBusy=false;$('qaBtn').disabled=false;$('qaBtn').className='qa-btn';$('qaBtn').textContent='🎙 问小K';qaRender({status:'error',error:'录音太短，请再说一次。',updated_at:Date.now()/1000});return;}var joined=qaFloatConcat(parts),down=qaDownsample(joined,rate,16000),wav=qaWav(down,16000);qaSend(wav);}
  function qaSend(wav){qaBusy=true;var scheme=(location.protocol==='https:')?'wss:':'ws:',rid='kq-'+String(Date.now())+'-'+Math.floor(Math.random()*10000);qaRender({status:'uploading',request_id:rid,updated_at:Date.now()/1000});try{qaSocket=new WebSocket(scheme+'//'+location.host+'/kitchen/ws');qaSocket.binaryType='arraybuffer';}catch(e){qaBusy=false;qaRender({status:'error',error:'无法建立语音上传连接',updated_at:Date.now()/1000});return;}var finished=false;qaSocket.onopen=function(){try{qaSocket.send(JSON.stringify({type:'kitchen.hello',protocol:kitchenProtocol,device_id:deviceId,capabilities:{touch:true,display:true,http_poll:true,timers:true,local_audio:true,media_audio:true,airplay:true,mic_probe:true,qa_ptt:true}}));qaSocket.send(JSON.stringify({type:'kitchen.qa.start',request_id:rid,format:'audio/wav',sample_rate:16000}));qaSocket.send(wav);qaSocket.send(JSON.stringify({type:'kitchen.qa.stop',request_id:rid}));}catch(e){qaRender({status:'error',error:'发送录音失败',updated_at:Date.now()/1000});}};qaSocket.onmessage=function(ev){try{var m=JSON.parse(ev.data);if(m.qa)qaRender(m.qa);if(m.type==='kitchen.qa.transcript'&&m.text)$('qaTranscript').textContent='你：'+m.text;if(m.type==='kitchen.qa.answer'&&m.text)$('qaAnswer').textContent='小K：'+m.text;if(m.type==='kitchen.qa.result'){finished=true;qaBusy=false;if(m.qa)qaRender(m.qa);if(m.audio)syncAudio(m.audio);try{qaSocket.close();}catch(e){}}if(m.type==='kitchen.qa.error'){finished=true;qaBusy=false;qaRender(m.qa||{status:'error',error:m.message||'语音问答失败',updated_at:Date.now()/1000});try{qaSocket.close();}catch(e){}}}catch(e){}};qaSocket.onerror=function(){};qaSocket.onclose=function(){qaSocket=null;if(!finished){/* HTTP polling is authoritative and can recover the final result. */}};}
  function qaTap(ev){if(ev){try{ev.preventDefault();}catch(e){}}var qd=$('qaDock');if(qd)qd.className='qa-dock qa-active';qaStart();}if(window.PointerEvent){$('qaBtn').addEventListener('pointerup',qaTap,false);}else{$('qaBtn').addEventListener('click',qaTap,false);}$('qaHeaderBtn').onclick=qaTap;$('qaClear').onclick=function(){$('qaTranscript').textContent='';$('qaAnswer').textContent='';action('qa_dismiss',{},function(){qaRender({status:'idle',updated_at:Date.now()/1000});});};
  function playKitchenAudio(a){if(!a||!a.event_id)return;if(a.event_id===lastAudioId)return;pendingAudio=a;if(standaloneAlarmId||!audioUnlocked||audioPlaying)return;if(Number(a.expires_at||0)>0&&Number(a.expires_at)<Date.now()/1000){lastAudioId=a.event_id;pendingAudio=null;return;}var p=initKitchenPlayer();if(!p)return;audioPlaying=true;p.onended=function(){audioPlaying=false;lastAudioId=a.event_id;pendingAudio=null;ackAudio(a.event_id);audioButtonState();};p.onerror=function(){audioPlaying=false;console.log('kitchen media playback failed code='+(p.error?p.error.code:'unknown'));audioButtonState();};try{p.src=a.url+'&_='+Date.now();p.load();var started=p.play();if(started&&started.catch){started.catch(function(e){audioPlaying=false;console.log('kitchen media play rejected',e);audioButtonState();});}}catch(e){audioPlaying=false;console.log('kitchen media playback failed',e);audioButtonState();}}
  function timerCenterOpen(){return $('timerCenterModal')&&$('timerCenterModal').classList.contains('show');}
  function renderTimerCenter(){var host=$('timerCenterHost');if(!host)return;host.innerHTML='';var body=el('div','timer-center-body'),grid=el('div','timer-center-grid'),arr=latestTimers.slice(0);arr.sort(function(a,b){if(a.status==='finished'&&b.status!=='finished')return -1;if(b.status==='finished'&&a.status!=='finished')return 1;return Number(a.ends_at||9e18)-Number(b.ends_at||9e18);});if(!arr.length){grid.appendChild(el('div','timer-center-empty','目前没有正在运行的计时器。\n可以直接从下方快速新建一个厨房通用计时。'));}else{for(var i=0;i<Math.min(arr.length,6);i++){(function(t){var card=el('section','timer-center-item '+t.status),tag=el('div','timer-center-tag');tag.appendChild(el('span','',t.kind==='standalone'?'厨房通用':(t.dish||'菜品')));tag.appendChild(el('small','',t.kind==='standalone'?'独立计时':'第 '+(Number(t.step)+1)+' 步'));card.appendChild(tag);var tm=el('div','timer-center-time',t.status==='finished'?'时间到':fmt(remaining(t)));tm.setAttribute('data-r49-timer-time',t.timer_id);card.appendChild(tm);var acts=el('div','timer-center-actions');if(t.status==='running'){var pause=el('button','primary','暂停');pause.onclick=function(){action('timer_pause',{timer_id:t.timer_id});};acts.appendChild(pause);}else if(t.status==='paused'){var resume=el('button','primary','继续');resume.onclick=function(){action('timer_resume',{timer_id:t.timer_id});};acts.appendChild(resume);}else{var done=el('button','primary','结束提醒');done.onclick=function(){action('timer_dismiss',{timer_id:t.timer_id});};acts.appendChild(done);}var open=el('button',t.status==='finished'?'danger':'',t.kind==='standalone'?'设置':'回到菜谱');open.onclick=function(){if(t.kind==='standalone'){if(t.status==='finished')action('timer_dismiss',{timer_id:t.timer_id});else{closeTimerCenter();openStandaloneTimer();}}else{closeTimerCenter();action('timer_open',{timer_id:t.timer_id});}};acts.appendChild(open);card.appendChild(acts);grid.appendChild(card);})(arr[i]);}}
    body.appendChild(grid);var quick=el('div','timer-center-quick');quick.appendChild(el('strong','','快速新建'));[[60,'1分钟'],[300,'5分钟'],[600,'10分钟'],[900,'15分钟'],[1800,'30分钟']].forEach(function(x){var b=el('button','',''+x[1]);b.onclick=function(){action('timer_standalone_start',{seconds:x[0]});};quick.appendChild(b);});var custom=el('button','primary','自定义');custom.onclick=function(){closeTimerCenter();openStandaloneTimer();};quick.appendChild(custom);body.appendChild(quick);host.appendChild(body);}
  function openTimerCenter(){unlockAudio();$('timerCenterModal').className='modal timer-center-modal show';renderTimerCenter();}
  function closeTimerCenter(){$('timerCenterModal').className='modal timer-center-modal';}
  $('timerCenterClose').onclick=closeTimerCenter;$('timerCenterModal').onclick=function(ev){if(ev.target===$('timerCenterModal'))closeTimerCenter();};
  function finishedStandalone(){for(var i=0;i<latestTimers.length;i++){var t=latestTimers[i];if(t.kind==='standalone'&&t.status==='finished')return t;}return null;}
  function standaloneTimer(){for(var i=0;i<latestTimers.length;i++){if(latestTimers[i].kind==='standalone')return latestTimers[i];}return null;}
  function startStandaloneAlarm(t){if(!t||!t.timer_id||standaloneAlarmId===t.timer_id)return;if(audioPlaying)return;var p=initKitchenPlayer();if(!p||!audioUnlocked)return;standaloneAlarmId=t.timer_id;try{p.onended=null;p.onerror=function(){console.log('standalone timer alarm playback failed');};p.loop=true;p.src='/kitchen/alarm.wav?_='+Date.now();p.load();var started=p.play();if(started&&started.catch){started.catch(function(e){standaloneAlarmId='';p.loop=false;console.log('standalone timer alarm rejected',e);});}}catch(e){standaloneAlarmId='';try{p.loop=false;}catch(_e){}console.log('standalone timer alarm failed',e);}}
  function stopStandaloneAlarm(){if(!standaloneAlarmId)return;standaloneAlarmId='';var p=initKitchenPlayer();if(p){try{p.loop=false;p.pause();p.removeAttribute('src');p.load();}catch(e){}}audioPlaying=false;if(pendingAudio)playKitchenAudio(pendingAudio);}
  function syncStandaloneAlarm(){var t=finishedStandalone();if(t){startStandaloneAlarm(t);}else{stopStandaloneAlarm();}}
  function syncAudio(a){if(!a||!a.event_id)return;if(a.event_id===lastAudioId)return;pendingAudio=a;playKitchenAudio(a);}
  function ackAudio(id){if(!id)return;xhrGet('/kitchen/action?action=audio_ack&value='+encodeURIComponent(id),function(){});}
  function action(name,params,cb){var url='/kitchen/action?action='+encodeURIComponent(name),k;params=params||{};for(k in params){if(params.hasOwnProperty(k)&&params[k]!==undefined&&params[k]!==null&&params[k]!==''){url+='&'+encodeURIComponent(k)+'='+encodeURIComponent(String(params[k]));}}xhrGet(url,function(err,text){if(err){try{var er=JSON.parse(text||'{}');if(er&&er.error)err.serverMessage=String(er.error);}catch(ignore){}if(cb)cb(err);else showError(err.serverMessage||('操作失败：'+err.message));return;}var r=null;try{r=JSON.parse(text);}catch(e){showError('操作响应解析失败');if(cb)cb(e);return;}try{if(r&&r.timers)syncTimers(r.timers);if(r&&r.audio)syncAudio(r.audio);if(r&&r.qa)qaRender(r.qa);if(r&&r.view)render(r.view,true);}catch(e){console.log('Kitchen view render failed after successful action',e);if(!cb)showError('页面刷新失败，请稍后重试');}if(cb)cb(null,r);});}
  function navButton(text,name,primary){var b=el('button',primary?'primary':'',text);b.onclick=function(){action(name);};return b;}
  function fmt(sec){sec=Math.max(0,Math.ceil(Number(sec)||0));var m=Math.floor(sec/60),s=sec%60;return (m<10?'0':'')+m+':'+(s<10?'0':'')+s;}
  function clientClockMs(){try{if(window.performance&&typeof window.performance.now==='function')return window.performance.now();}catch(e){}return Date.now();}
  function anchorTimer(t){if(!t)return t;var precise=Number(t.remaining_precise_sec);if(!isFinite(precise))precise=Number(t.remaining_sec)||0;t._client_remaining=Math.max(0,precise);t._client_anchor_ms=clientClockMs();return t;}
  function remaining(t){if(!t)return 0;if(t.status==='running'){var base=Number(t._client_remaining);if(!isFinite(base))base=Number(t.remaining_precise_sec);if(!isFinite(base))base=Number(t.remaining_sec)||0;var anchor=Number(t._client_anchor_ms);var elapsed=isFinite(anchor)?Math.max(0,(clientClockMs()-anchor)/1000):0;return Math.max(0,base-elapsed);}return Math.max(0,Number(t.remaining_precise_sec)||Number(t.remaining_sec)||0);}
  function timerForView(v){if(!v||v.type!=='kitchen.show_recipe')return null;for(var i=0;i<latestTimers.length;i++){var t=latestTimers[i];if(t.dish===v.title&&Number(t.step)===Number(v.step))return t;}return null;}
  function stepSize(sec){sec=Number(sec)||0;if(sec<=90)return 10;if(sec<=600)return 30;return 60;}
  function draftKey(v){return (v&&v.title?v.title:'')+'#'+String(v&&v.step!==undefined?v.step:0);}
  function suggested(v){var key=draftKey(v);if(Object.prototype.hasOwnProperty.call(drafts,key))return drafts[key];var hint=v&&v.timer_hint?v.timer_hint:null;if(!hint||!hint.default_sec)return 0;var sec=Number(hint.default_sec)||0;sec=Math.max(5,Math.min(5999,sec));drafts[key]=sec;return sec;}
  function setDraft(v,sec){sec=Math.max(5,Math.min(5999,Math.round(sec)));drafts[draftKey(v)]=sec;renderStepTimer(v);}
  function renderTimerStrip(){var strip=$('timerStrip');if(!strip)return;strip.innerHTML='';var shown=0;for(var i=0;i<latestTimers.length;i++){(function(t){if(t.status==='finished'&&t.kind!=='standalone'&&currentView&&currentView.type==='kitchen.show_recipe'&&t.dish===currentView.title&&Number(t.step)===Number(currentView.step)){return;}var chip=el('div','timer-chip '+t.status);var label=t.kind==='standalone'?'独立计时':(t.dish+' · '+(Number(t.step)+1)+'步');var right=t.status==='finished'?'时间到':fmt(remaining(t));chip.appendChild(el('span','',label));chip.appendChild(el('strong','',right));chip.onclick=function(){if(t.kind==='standalone'){openStandaloneTimer();}else{action('timer_open',{timer_id:t.timer_id});}};if(t.status==='finished'){var x=el('button','chip-x','×');x.setAttribute('aria-label','关闭提醒');x.onclick=function(ev){if(ev&&ev.stopPropagation)ev.stopPropagation();action('timer_dismiss',{timer_id:t.timer_id});};chip.appendChild(x);}strip.appendChild(chip);shown++;})(latestTimers[i]);}strip.style.display=shown?'flex':'none';}
  function syncTimers(timers){latestTimers=(timers&&timers.length!==undefined)?timers:[];for(var ai=0;ai<latestTimers.length;ai++)anchorTimer(latestTimers[ai]);renderTimerStrip();syncStandaloneAlarm();if(currentView&&currentView.type==='kitchen.show_recipe')renderStepTimer(currentView);if(currentView&&currentView.type==='kitchen.show_dashboard')renderDashboardTimers(currentView);if(timerCenterOpen())renderTimerCenter();if($('idleTimerHost'))renderIdleStandaloneTimer();}
  function timerButton(text,fn,cls){var b=el('button',cls||'',text);b.onclick=fn;return b;}
  var standaloneTimerMode='countdown',standaloneDraftSec=900;
  var stopwatchState={running:false,elapsed_ms:0,started_ms:0};
  try{var sw=JSON.parse(localStorage.getItem('kitchen.stopwatch.v1')||'{}');if(sw&&typeof sw==='object'){stopwatchState.running=!!sw.running;stopwatchState.elapsed_ms=Math.max(0,Number(sw.elapsed_ms)||0);stopwatchState.started_ms=Math.max(0,Number(sw.started_ms)||0);}}catch(e){}
  function saveStopwatch(){try{localStorage.setItem('kitchen.stopwatch.v1',JSON.stringify(stopwatchState));}catch(e){}}
  function stopwatchSeconds(){var ms=Number(stopwatchState.elapsed_ms)||0;if(stopwatchState.running&&stopwatchState.started_ms)ms+=Math.max(0,Date.now()-stopwatchState.started_ms);return Math.floor(ms/1000);}
  function stopwatchStart(){if(!stopwatchState.running){stopwatchState.running=true;stopwatchState.started_ms=Date.now();saveStopwatch();renderIdleStandaloneTimer();}}
  function stopwatchPause(){if(stopwatchState.running){stopwatchState.elapsed_ms=Math.max(0,(Number(stopwatchState.elapsed_ms)||0)+(Date.now()-stopwatchState.started_ms));stopwatchState.running=false;stopwatchState.started_ms=0;saveStopwatch();renderIdleStandaloneTimer();}}
  function stopwatchReset(){stopwatchState={running:false,elapsed_ms:0,started_ms:0};saveStopwatch();renderIdleStandaloneTimer();}
  function overlayAction(name,params,cb){var url='/kitchen/action?action='+encodeURIComponent(name),k;params=params||{};for(k in params){if(params.hasOwnProperty(k)&&params[k]!==undefined&&params[k]!==null&&params[k]!==''){url+='&'+encodeURIComponent(k)+'='+encodeURIComponent(String(params[k]));}}xhrGet(url,function(err,text){if(err){if(cb)cb(err);else showError('操作失败：'+err.message);return;}var r=null;try{r=JSON.parse(text);}catch(e){if(cb)cb(e);else showError('操作响应解析失败');return;}try{if(r&&r.timers)syncTimers(r.timers);if(r&&r.audio)syncAudio(r.audio);if(r&&r.qa)qaRender(r.qa);}catch(ignore){}if(cb)cb(null,r);});}
  function renderShoppingOverlay(v){var host=$('shoppingModalHost');if(!host)return;host.innerHTML='';var groups=(v&&v.groups)||[];if(!groups.length){host.appendChild(el('div','shopping-modal-empty','今天暂时没有需要采购的内容。'));return;}for(var i=0;i<groups.length;i++){var card=el('section','mock-shopping-card');card.appendChild(el('strong','',groups[i].name||'建议购买'));var items=groups[i].items||[],list=el('div','mock-shopping-list');for(var j=0;j<items.length;j++){(function(item){var text=(item&&typeof item==='object')?String(item.text||''):String(item||''),itemId=(item&&typeof item==='object')?String(item.id||''):'',inv=(item&&typeof item==='object')?item.inventory:null;var lab=el('label','mock-shopping-row'+((item&&item.checked)?' checked':'')),ck=document.createElement('input');ck.type='checkbox';ck.checked=!!(item&&item.checked);ck.onchange=function(){var wanted=ck.checked;ck.disabled=true;overlayAction('shopping_toggle_overlay',{value:itemId,done:wanted?'1':'0'},function(err,r){ck.disabled=false;if(err){ck.checked=!wanted;return;}renderShoppingOverlay((r&&r.overlay)||v);});};lab.appendChild(ck);var copy=el('span','shopping-item-copy');copy.appendChild(el('span','shopping-item-text',text));lab.appendChild(copy);if(inv&&inv.label)lab.appendChild(el('span','shopping-stock-badge',String(inv.label)));list.appendChild(lab);})(items[j]);}card.appendChild(list);host.appendChild(card);}}
  function openShoppingModal(){var m=$('shoppingModal');if(!m)return;m.className='modal show';var h=$('shoppingModalHost');if(h)h.innerHTML='<div class="shopping-modal-empty">正在读取今日采购…</div>';overlayAction('shopping_overlay',{},function(err,r){if(err){if(h)h.innerHTML='<div class="shopping-modal-empty">采购清单读取失败，请稍后再试。</div>';return;}renderShoppingOverlay((r&&r.overlay)||{});});}
  function closeShoppingModal(){if($('shoppingModal'))$('shoppingModal').className='modal';}
  if($('shoppingModalClose'))$('shoppingModalClose').onclick=closeShoppingModal;
  if($('shoppingModal'))$('shoppingModal').onclick=function(ev){if(ev.target===$('shoppingModal'))closeShoppingModal();};
  function openStandaloneTimer(){$('standaloneTimerModal').className='modal show';try{renderIdleStandaloneTimer();}catch(e){console.log('Kitchen standalone timer render failed',e);var h=$('idleTimerHost');if(h){h.innerHTML='';var box=el('div','kitchen-timer-ui');box.appendChild(el('div','kitchen-timer-big',fmt(standaloneDraftSec)));box.appendChild(el('div','kitchen-timer-sub','计时器正在恢复，请重新打开一次'));h.appendChild(box);}}}
  function closeStandaloneTimer(){$('standaloneTimerModal').className='modal';}
  $('standaloneTimerClose').onclick=closeStandaloneTimer;
  $('standaloneTimerModal').onclick=function(ev){if(ev.target===$('standaloneTimerModal'))closeStandaloneTimer();};
  function kitchenTimerModeButton(label,mode){var b=el('button',standaloneTimerMode===mode?'active':'',label);b.type='button';b.onclick=function(){standaloneTimerMode=mode;renderIdleStandaloneTimer();};return b;}
  function countdownStartButton(t){var b=el('button','kitchen-timer-main',t?(t.status==='running'?'暂停':(t.status==='paused'?'继续':'结束提醒')):'▶ 开始计时');b.type='button';b.onclick=function(){if(!t)action('timer_standalone_start',{seconds:standaloneDraftSec});else if(t.status==='running')action('timer_pause',{timer_id:t.timer_id});else if(t.status==='paused')action('timer_resume',{timer_id:t.timer_id});else action('timer_dismiss',{timer_id:t.timer_id});};return b;}
  function renderIdleStandaloneTimer(){var host=$('idleTimerHost');if(!host)return;host.innerHTML='';var root=el('div','kitchen-timer-ui');var modes=el('div','kitchen-timer-mode');modes.appendChild(kitchenTimerModeButton('倒计时','countdown'));modes.appendChild(kitchenTimerModeButton('正计时','stopwatch'));root.appendChild(modes);
    if(standaloneTimerMode==='stopwatch'){var swt=el('div','kitchen-timer-big',fmt(stopwatchSeconds()));swt.id='stopwatchTime';root.appendChild(swt);root.appendChild(el('div','kitchen-timer-sub',stopwatchState.running?'正在正计时':'适合记录焯水、醒面或自由计时'));var main=el('button','kitchen-timer-main',stopwatchState.running?'暂停':'▶ 开始计时');main.type='button';main.onclick=stopwatchState.running?stopwatchPause:stopwatchStart;root.appendChild(main);var sacts=el('div','kitchen-timer-secondary');var rs=el('button','','重置');rs.type='button';rs.onclick=stopwatchReset;sacts.appendChild(rs);var noop=el('button','','');noop.style.visibility='hidden';noop.disabled=true;sacts.appendChild(noop);var cl=el('button','','关闭');cl.type='button';cl.onclick=closeStandaloneTimer;sacts.appendChild(cl);root.appendChild(sacts);host.appendChild(root);return;}
    var t=standaloneTimer();if(!t){var presets=el('div','kitchen-timer-presets');[[60,'1分钟'],[300,'5分钟'],[600,'10分钟'],[900,'15分钟'],[1800,'30分钟'],[3600,'1小时']].forEach(function(x){var b=el('button','kitchen-timer-preset'+(standaloneDraftSec===x[0]?' active':''),x[1]);b.type='button';b.onclick=function(){standaloneDraftSec=x[0];renderIdleStandaloneTimer();};presets.appendChild(b);});root.appendChild(presets);root.appendChild(el('div','kitchen-timer-big',fmt(standaloneDraftSec)));root.appendChild(el('div','kitchen-timer-sub','倒计时结束后会持续提醒，直到你主动结束'));root.appendChild(countdownStartButton(null));}
    else{var sec=t.status==='finished'?0:remaining(t);var big=el('div','kitchen-timer-big',t.status==='finished'?'时间到':fmt(sec));big.id='idleStandaloneTime';root.appendChild(big);root.appendChild(el('div','kitchen-timer-sub',t.status==='running'?'倒计时进行中':(t.status==='paused'?'已暂停':'提醒中')));root.appendChild(countdownStartButton(t));var c=el('div','kitchen-timer-secondary');if(t.status!=='finished'){var plus=el('button','','＋1分钟');plus.type='button';plus.onclick=function(){action('timer_adjust',{timer_id:t.timer_id,seconds:60});};c.appendChild(plus);var reset=el('button','','重新设置');reset.type='button';reset.onclick=function(){openModal(Math.max(5,remaining(t)||t.duration_sec),{timer:t});};c.appendChild(reset);var cancel=el('button','danger','取消');cancel.type='button';cancel.onclick=function(){action('timer_cancel',{timer_id:t.timer_id});};c.appendChild(cancel);}else{var done=el('button','danger','结束提醒');done.type='button';done.style.gridColumn='1 / -1';done.onclick=function(){action('timer_dismiss',{timer_id:t.timer_id});};c.appendChild(done);}root.appendChild(c);}
    host.appendChild(root);
  }
  var pendingTodayRemoveDish='';
  function openTodayRemoveModal(label){pendingTodayRemoveDish=String(label||'');$('todayRemoveName').textContent=pendingTodayRemoveDish;$('todayRemoveStatus').textContent='';$('todayRemoveConfirm').disabled=false;$('todayRemoveModal').className='modal show';}
  function closeTodayRemoveModal(){$('todayRemoveModal').className='modal';pendingTodayRemoveDish='';$('todayRemoveStatus').textContent='';$('todayRemoveConfirm').disabled=false;}
  $('todayRemoveCancel').onclick=closeTodayRemoveModal;$('todayRemoveModal').onclick=function(ev){if(ev.target===$('todayRemoveModal'))closeTodayRemoveModal();};
  $('todayRemoveConfirm').onclick=function(){if(!pendingTodayRemoveDish)return;var dish=pendingTodayRemoveDish,btn=$('todayRemoveConfirm');btn.disabled=true;$('todayRemoveStatus').textContent='正在从今日菜谱移除…';action('today_remove',{value:dish},function(err){if(err){btn.disabled=false;$('todayRemoveStatus').textContent='移除失败：'+(err.serverMessage||err.message||'请稍后再试');return;}closeTodayRemoveModal();setTimeout(poll,60);});};
  function renderStepTimer(v){var host=$('stepTimerHost');if(!host)return;host.innerHTML='';var hint=v.timer_hint||null,t=timerForView(v);
    // Untimed steps intentionally render no step-timer UI. Every cooking page
    // already has the standalone Kitchen Timer, so a second generic timer button
    // only adds clutter. Existing/running recipe timers remain visible.
    if(!t&&!hint)return;
    var box=el('div','step-timer'+(t&&t.status==='finished'?' finished':''));var left=el('div');left.appendChild(el('div','timer-title',t?'本步骤计时':'建议计时'));var sec=t?remaining(t):suggested(v);var timeEl=el('div','timer-time',fmt(sec));timeEl.id='currentTimerTime';timeEl.onclick=function(){openModal(Math.max(5,sec),t?{timer:t}:{view:v});};left.appendChild(timeEl);var state='';if(t){state=t.status==='running'?'计时中':(t.status==='paused'?'已暂停':'时间到');}else{state='建议 '+fmt(hint.default_sec)+(Number(hint.max_sec)>Number(hint.default_sec)?' · 最长 '+fmt(hint.max_sec):'');}left.appendChild(el('div','timer-state',state));box.appendChild(left);var controls=el('div','timer-controls');var step=stepSize(sec);
    if(!t){controls.appendChild(timerButton('－'+fmt(step),function(){setDraft(v,suggested(v)-step);}));controls.appendChild(timerButton('▶ 开始',function(){action('timer_start',{seconds:suggested(v)});},'primary timer-touch-main'));controls.appendChild(timerButton('＋'+fmt(step),function(){setDraft(v,suggested(v)+step);}));controls.appendChild(timerButton('设置',function(){openModal(suggested(v),{view:v});}));}
    else if(t.status==='running'){controls.appendChild(timerButton('－'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:-step});}));controls.appendChild(timerButton('Ⅱ 暂停',function(){action('timer_pause',{timer_id:t.timer_id});},'primary timer-touch-main'));controls.appendChild(timerButton('＋'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:step});}));controls.appendChild(timerButton('取消',function(){action('timer_cancel',{timer_id:t.timer_id});},'danger'));}
    else if(t.status==='paused'){controls.appendChild(timerButton('－'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:-step});}));controls.appendChild(timerButton('▶ 继续',function(){action('timer_resume',{timer_id:t.timer_id});},'primary timer-touch-main'));controls.appendChild(timerButton('＋'+fmt(step),function(){action('timer_adjust',{timer_id:t.timer_id,seconds:step});}));controls.appendChild(timerButton('设置',function(){openModal(sec,{timer:t});}));}
    else{controls.appendChild(timerButton('＋'+fmt(step)+'继续',function(){action('timer_adjust',{timer_id:t.timer_id,seconds:step});},'primary timer-touch-main'));controls.appendChild(timerButton('重新计时',function(){action('timer_start',{seconds:Math.max(5,suggested(v)||300)});}));controls.appendChild(timerButton('完成',function(){action('timer_dismiss',{timer_id:t.timer_id});}));}
    box.appendChild(controls);host.appendChild(box);
  }
  function tickTimers(){renderTimerStrip();if(currentView&&currentView.type==='kitchen.show_recipe'){var t=timerForView(currentView),n=$('currentTimerTime');if(t&&n)n.textContent=fmt(remaining(t));}var nodes=document.querySelectorAll('[data-r49-timer-time]');for(var ri=0;ri<nodes.length;ri++){var id=nodes[ri].getAttribute('data-r49-timer-time'),rt=null;for(var rj=0;rj<latestTimers.length;rj++){if(latestTimers[rj].timer_id===id){rt=latestTimers[rj];break;}}if(rt)nodes[ri].textContent=rt.status==='finished'?'时间到':fmt(remaining(rt));}var st=standaloneTimer(),sn=$('idleStandaloneTime');if(st&&sn&&st.status!=='finished')sn.textContent=fmt(remaining(st));var swt=$('stopwatchTime');if(swt&&standaloneTimerMode==='stopwatch')swt.textContent=fmt(stopwatchSeconds());}
  function render(v,force){
    if(!v||!v.type)return;var rev=String(v.revision||'');if(!force&&rev&&rev===lastRevision){currentView=v;return;}if(rev)lastRevision=rev;currentView=v;clearView();updateGlobalNav(navActiveForView(v));
    if(v.type==='kitchen.show_idle'){renderModernHome(v);return;}if(v.type==='kitchen.show_dashboard'){renderDashboard(v);return;}if(v.type==='kitchen.show_menu'){renderTodayMenu(v);return;}if(v.type==='kitchen.show_picker'){renderPicker(v);return;}if(v.type==='kitchen.show_prep'){renderPrep(v);return;}if(v.type==='kitchen.show_recipe'){renderCookingFlow(v);return;}if(v.type==='kitchen.show_consumption'){renderConsumption(v);return;}if(v.type==='kitchen.show_finish'){renderFinish(v);return;}if(v.type==='kitchen.show_save_private'){renderSavePrivate(v);return;}if(v.type==='kitchen.show_shopping'){renderShopping(v);return;}if(v.type==='kitchen.show_timeline'){renderTimeline(v);return;}
    setHomeMode(false);
    $('eyebrow').textContent=v.eyebrow||'HOME AI · 厨房';$('title').textContent=v.title||'小K';$('message').textContent=v.message||'';$('footer').textContent=(v.footer||'')+' · __KITCHEN_UI_VERSION__';
    if(v.type==='kitchen.show_done'){action('home');return;}
    if(v.type==='kitchen.show_message')return;
    if(v.type==='kitchen.show_menu'){var grid=el('div','menu');var items=(v.items&&v.items.length!==undefined)?v.items:[];for(var i=0;i<items.length;i++){(function(index){var item=items[index];var label=(typeof item==='string')?item:((item&&item.name)?item.name:('菜品 '+(index+1)));var b=el('button','');b.appendChild(el('span','',label));if(item&&item.has_progress&&Number(item.total_steps)>0){var pstep=Number(item.progress_step)||0;var ptxt=pstep===0?'继续 · 备菜':('继续 · 第 '+(pstep+1)+' / '+Number(item.total_steps)+' 步');b.appendChild(el('span','menu-progress',ptxt));}b.onclick=function(){action('recipe',{value:label});};grid.appendChild(b);})(i);}$('content').appendChild(grid);var tools=el('div','toolbar');var shop=el('button','action','购物清单');shop.onclick=function(){action('shopping');};tools.appendChild(shop);var time=el('button','action','烧菜顺序');time.onclick=function(){action('timeline');};tools.appendChild(time);var finish=el('button','action finish-btn','结束今日烹饪');finish.onclick=function(){action('finish_start');};tools.appendChild(finish);$('content').appendChild(tools);return;}
    if(v.type==='kitchen.show_recipe'){$('message').textContent='';var metaText=(v.type_label||'菜谱')+(v.estimated_text?' · '+v.estimated_text:'');$('content').appendChild(el('div','recipe-meta',metaText));var card=el('div','step-card');var stepNum=(parseInt(v.step,10)||0)+1,total=parseInt(v.total_steps,10)||1;var stepLabel=(v.step_kind==='prep')?('备菜 · '+stepNum+' / '+total):('步骤 '+stepNum+' / '+total);card.appendChild(el('div','step-label',stepLabel));card.appendChild(el('div','step-text',v.step_text||'（本步骤内容为空）'));var timerHost=el('div','');timerHost.id='stepTimerHost';card.appendChild(timerHost);var tips=v.key_points||[];if(tips.length){var box=el('div','tips');box.appendChild(el('h3','','关键提醒'));var ul=el('ul');for(var j=0;j<tips.length;j++){ul.appendChild(el('li','',tips[j]));}box.appendChild(ul);card.appendChild(box);}$('content').appendChild(card);renderStepTimer(v);$('nav').style.display='grid';$('nav').appendChild(navButton('← 上一步','prev',false));$('nav').appendChild(navButton('返回菜单','menu',false));$('nav').appendChild(navButton('下一步 →','next',true));return;}
    if(v.type==='kitchen.show_finish'){var wrap=el('div','step-card');wrap.appendChild(el('div','step-label','今日收尾'));wrap.appendChild(el('div','step-text',v.message||'确认结束今天的烹饪？'));var fa=el('div','finish-actions');var back=el('button','','继续烹饪');back.onclick=function(){action('menu');};fa.appendChild(back);var yes=el('button','danger','确认结束');yes.onclick=function(){action('finish_confirm');};fa.appendChild(yes);wrap.appendChild(fa);$('content').appendChild(wrap);return;}
    if(v.type==='kitchen.show_save_private'){var items2=v.items||[],list=el('div','choice-list');for(var z=0;z<items2.length;z++){var row=el('label','choice-row');var ck=document.createElement('input');ck.type='checkbox';ck.value=items2[z];ck.className='private-choice';row.appendChild(ck);row.appendChild(el('span','',items2[z]));list.appendChild(row);}$('content').appendChild(list);var sa=el('div','finish-actions');var none=el('button','','不保存，直接结束');none.onclick=function(){action('finish_no_save');};sa.appendChild(none);var save=el('button','primary','保存所选并结束');save.onclick=function(){var picked=[],nodes=document.querySelectorAll('.private-choice:checked');for(var n=0;n<nodes.length;n++)picked.push(nodes[n].value);action('finish_save',{value:JSON.stringify(picked)});};sa.appendChild(save);$('content').appendChild(sa);return;}
    if(v.type==='kitchen.show_shopping'){var groups=v.groups||[];for(var g=0;g<groups.length;g++){var box2=el('section','list-group');box2.appendChild(el('h3','',groups[g].name||''));var ul2=el('ul'),gi=groups[g].items||[];for(var q=0;q<gi.length;q++){ul2.appendChild(el('li','',gi[q]));}box2.appendChild(ul2);$('content').appendChild(box2);}$('nav').style.display='grid';$('nav').style.gridTemplateColumns='1fr';$('nav').appendChild(navButton('返回今日菜单','menu',true));return;}
    if(v.type==='kitchen.show_timeline'){var ol=el('ol','timeline'),ti=v.items||[];for(var k=0;k<ti.length;k++){ol.appendChild(el('li','',ti[k]));}$('content').appendChild(ol);$('nav').style.display='grid';$('nav').style.gridTemplateColumns='1fr';$('nav').appendChild(navButton('返回今日菜单','menu',true));return;}
    showError('未知页面类型：'+v.type);
  }
  function poll(){xhrGet('/kitchen/view',function(err,text){if(err)return;try{var m=JSON.parse(text);if(m&&m.timers)syncTimers(m.timers);if(m&&m.audio)syncAudio(m.audio);if(m&&m.qa)qaRender(m.qa);if(m&&m.view)render(m.view,false);}catch(e){showError('Gateway 状态解析失败');}});}
  function connectWS(){var scheme=(location.protocol==='https:')?'wss:':'ws:';try{ws=new WebSocket(scheme+'//'+location.host+'/kitchen/ws');}catch(e){wsOK=false;setStatus();return;}ws.onopen=function(){retry=1000;wsOK=true;setStatus();try{ws.send(JSON.stringify({type:'kitchen.hello',protocol:kitchenProtocol,device_id:deviceId,capabilities:{touch:true,display:true,http_poll:true,timers:true,local_audio:true,media_audio:true,airplay:true,mic_probe:true,qa_ptt:true}}));}catch(e){}};ws.onmessage=function(ev){try{var m=JSON.parse(ev.data);if(m.type==='kitchen.ready'){wsOK=true;setStatus();return;}if(m.type==='kitchen.sync'&&m.view){render(m.view,false);return;}if(m.type&&m.type.indexOf('kitchen.show_')===0){render(m,false);return;}}catch(e){}};ws.onclose=function(){wsOK=false;setStatus();setTimeout(connectWS,retry);retry=Math.min(Math.floor(retry*1.6),10000);};ws.onerror=function(){try{ws.close();}catch(e){}};}
  window.onerror=function(msg){showError('页面脚本错误：'+String(msg));return false;};
  poll();setInterval(poll,1000);setInterval(tickTimers,250);connectWS();
})();
</script>
</body>
</html>
'''
    html = (
        html.replace("__KITCHEN_UI_VERSION__", KITCHEN_UI_VERSION)
        .replace("__KITCHEN_PROTOCOL__", KITCHEN_PROTOCOL)
        .replace("__KITCHEN_QA_MAX_MS__", str(KITCHEN_QA_MAX_SEC * 1000))
    )
    return html.encode("utf-8")


def _http_response(
    status: int, reason: str, body: bytes, content_type: str, *, extra_headers: dict[str, str] | None = None
) -> Response:
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    headers["Pragma"] = "no-cache"
    headers["Expires"] = "0"
    headers["X-Content-Type-Options"] = "nosniff"
    for key, value in (extra_headers or {}).items():
        headers[key] = value
    return Response(status, reason, headers, body)


def _kitchen_audio_http_response(wav: bytes, range_header: str = "") -> Response:
    """Serve WAV media with byte-range support for iOS HTMLMediaElement/AirPlay."""
    total = len(wav)
    base_headers = {"Accept-Ranges": "bytes"}
    raw = str(range_header or "").strip()
    if not raw:
        return _http_response(200, "OK", wav, "audio/wav", extra_headers=base_headers)

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw)
    if not match or total <= 0:
        return _http_response(416, "Range Not Satisfiable", b"", "audio/wav", extra_headers={
            "Accept-Ranges": "bytes", "Content-Range": f"bytes */{total}"
        })

    start_text, end_text = match.groups()
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else total - 1
    else:
        suffix = int(end_text or "0")
        if suffix <= 0:
            return _http_response(416, "Range Not Satisfiable", b"", "audio/wav", extra_headers={
                "Accept-Ranges": "bytes", "Content-Range": f"bytes */{total}"
            })
        start = max(0, total - suffix)
        end = total - 1

    if start < 0 or start >= total or end < start:
        return _http_response(416, "Range Not Satisfiable", b"", "audio/wav", extra_headers={
            "Accept-Ranges": "bytes", "Content-Range": f"bytes */{total}"
        })
    end = min(end, total - 1)
    part = wav[start:end + 1]
    return _http_response(206, "Partial Content", part, "audio/wav", extra_headers={
        "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{total}"
    })


async def gateway_http_request(connection: Any, request: Any) -> Response | None:
    global KITCHEN_CURRENT_STATE
    # Serve KitchenTerminal HTTP without creating a second web server.
    raw_path = str(getattr(request, "path", "") or "")
    parsed = urlsplit(raw_path)
    path = parsed.path
    query = parse_qs(parsed.query, keep_blank_values=True)
    if path in {WS_PATH, KITCHEN_WS_PATH, KITCHEN_CONTROL_PATH}:
        return None
    if path in {KITCHEN_HTTP_PATH, KITCHEN_HTTP_PATH + "/"}:
        return _http_response(200, "OK", _kitchen_html(), "text/html; charset=utf-8")
    if path == KITCHEN_HTTP_PATH + "/assets/home_hero.jpg":
        asset = Path(__file__).with_name("home_hero_crop.jpg")
        if asset.exists():
            return _http_response(200, "OK", asset.read_bytes(), "image/jpeg")
        return _http_response(404, "Not Found", b"asset not found", "text/plain; charset=utf-8")
    if path == KITCHEN_HTTP_PATH + "/assets/home_food.jpg":
        asset = Path(__file__).with_name("home_food_crop.jpg")
        if asset.exists():
            return _http_response(200, "OK", asset.read_bytes(), "image/jpeg")
        return _http_response(404, "Not Found", b"asset not found", "text/plain; charset=utf-8")
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
    if path == KITCHEN_HTTP_PATH + "/food/view":
        body = json.dumps(_food_snapshot(), ensure_ascii=False).encode("utf-8")
        return _http_response(200, "OK", body, "application/json; charset=utf-8")
    if path == KITCHEN_HTTP_PATH + "/food/action":
        action = str((query.get("action") or [""])[0]).strip().lower()
        name = str((query.get("name") or [""])[0]).strip()
        amount_text = str((query.get("amount") or [""])[0]).strip()
        unit = str((query.get("unit") or ["份"])[0]).strip() or "份"
        category = str((query.get("category") or ["其他"])[0]).strip() or "其他"
        source = str((query.get("source") or [""])[0]).strip()
        status = str((query.get("status") or [""])[0]).strip()
        priority = str((query.get("priority") or [""])[0]).strip()
        new_name = str((query.get("new_name") or [""])[0]).strip()
        quantity_text = str((query.get("quantity") or [""])[0]).strip()
        item_id_text = str((query.get("item_id") or [""])[0]).strip()
        try:
            if action == "add":
                _food_add(name, float(amount_text or "1"), unit, category, source)
            elif action == "consume":
                _food_consume(name, float(amount_text or "1"))
            elif action == "status":
                _food_set_status(name, status, category)
            elif action == "priority":
                _food_set_priority(name, priority)
            elif action == "rename":
                _food_rename(name, new_name)
            elif action == "edit":
                edited = _food_edit(name, new_name or name, None if quantity_text == "" else float(quantity_text), None if item_id_text == "" else int(item_id_text))
            else:
                raise ValueError(f"unsupported food action: {action}")
            payload = _food_snapshot()
            if action == "edit":
                payload["edited"] = edited
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return _http_response(200, "OK", body, "application/json; charset=utf-8")
        except (ValueError, TypeError, sqlite3.Error) as exc:
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
            return _http_response(400, "Bad Request", body, "application/json; charset=utf-8")

    if path == KITCHEN_HTTP_PATH + "/alarm.wav":
        request_headers = getattr(request, "headers", None)
        range_header = request_headers.get("Range", "") if request_headers is not None else ""
        return _kitchen_audio_http_response(KITCHEN_STANDALONE_ALARM_WAV, range_header)
    if path == KITCHEN_HTTP_PATH + "/audio":
        event_id = str((query.get("id") or [""])[0])
        wav = KITCHEN_AUDIO_CACHE.get(event_id)
        if wav is None:
            return _http_response(404, "Not Found", b"audio event not found", "text/plain; charset=utf-8")
        request_headers = getattr(request, "headers", None)
        range_header = request_headers.get("Range", "") if request_headers is not None else ""
        return _kitchen_audio_http_response(wav, range_header)

    if path == KITCHEN_HTTP_PATH + "/action":
        action = str((query.get("action") or [""])[0]).strip().lower()
        action = {
            "show_today": "today", "menu.today": "today", "menu.load": "today", "pull_today": "today",
            "show_home": "home", "menu.home": "home",
            "show_dashboard": "dashboard", "cook.dashboard": "dashboard", "menu.dashboard": "dashboard",
            "menu.picker": "picker", "show_picker": "picker",
            "menu.inventory_recommend": "inventory_recommend", "inventory.recommend": "inventory_recommend",
            "menu.add_recipe": "today_add", "menu.remove_recipe": "today_remove",
            "menu.prep": "prep", "show_prep": "prep", "prep.toggle": "prep_toggle",
            "recipe.open": "recipe", "menu.recipe": "recipe",
            "step.next": "next", "recipe.next": "next", "step.prev": "prev", "recipe.prev": "prev",
            "recipe.consume.open": "recipe_consume_open", "recipe.consume.commit": "recipe_consume_commit",
            "day.consume.commit": "day_consume_commit", "day.consume.skip": "day_consume_skip",
            "menu.back": "menu", "show_menu": "menu",
            "menu.shopping": "shopping", "show_shopping": "shopping", "shopping.toggle": "shopping_toggle",
            "shopping.overlay": "shopping_overlay", "shopping.overlay.toggle": "shopping_toggle_overlay",
            "menu.timeline": "timeline", "show_timeline": "timeline",
            "day.finish": "finish_start", "day.finish.confirm": "finish_confirm",
            "day.finish.none": "finish_no_save", "day.finish.save": "finish_save",
            "qa.dismiss": "qa_dismiss", "qa.clear": "qa_dismiss",
        }.get(action, action)
        value = str((query.get("value") or [""])[0]).strip()
        timer_id = str((query.get("timer_id") or [""])[0]).strip()
        seconds_text = str((query.get("seconds") or [""])[0]).strip()
        done_text = str((query.get("done") or [""])[0]).strip().lower()
        try:
            overlay_payload = None
            if action == "today":
                await _kitchen_show_today_menu()
            elif action == "home":
                await _kitchen_show_home()
            elif action == "dashboard":
                await _kitchen_show_dashboard()
            elif action == "picker":
                await _kitchen_show_picker()
            elif action == "inventory_recommend":
                try:
                    selected = json.loads(value) if value else []
                except json.JSONDecodeError as exc:
                    raise KitchenMenuError("invalid inventory selection") from exc
                if not isinstance(selected, list):
                    raise KitchenMenuError("invalid inventory selection")
                await _kitchen_show_inventory_recommendations([str(x) for x in selected])
            elif action == "today_add":
                menu = _kitchen_add_library_recipe_to_today(value)
                await _kitchen_show_menu(menu)
            elif action == "today_remove":
                menu = _kitchen_remove_recipe_from_today(value)
                await _kitchen_show_menu(menu)
            elif action == "prep":
                await _kitchen_show_prep()
            elif action == "prep_toggle":
                menu = KITCHEN_CURRENT_MENU or _kitchen_load()
                payload = _kitchen_set_prep_task(menu, value, done_text in {"1", "true", "yes", "on"})
                KITCHEN_CURRENT_STATE = {"screen": "prep", "date": str(menu.get("date") or ""), "dish": "", "step": 0}
                await kitchen_broadcast(payload)
            elif action == "recipe":
                delivered, recipe = await _kitchen_open_recipe_by_name(value, None)
                if recipe is None:
                    raise KitchenMenuError(f"recipe not found: {value}")
            elif action == "next":
                await _kitchen_move_step(1)
            elif action == "prev":
                await _kitchen_move_step(-1)
            elif action == "recipe_consume_open":
                await _kitchen_show_recipe_consumption(value)
            elif action == "recipe_consume_commit":
                try:
                    rows = json.loads(value) if value else []
                except json.JSONDecodeError as exc:
                    raise KitchenMenuError("invalid recipe consumption") from exc
                if not isinstance(rows, list):
                    raise KitchenMenuError("invalid recipe consumption")
                await _kitchen_commit_recipe_consumption(rows)
            elif action == "day_consume_commit":
                try:
                    rows = json.loads(value) if value else []
                except json.JSONDecodeError as exc:
                    raise KitchenMenuError("invalid daily consumption") from exc
                if not isinstance(rows, list):
                    raise KitchenMenuError("invalid daily consumption")
                await _kitchen_commit_day_consumption(rows, speak=True)
            elif action == "day_consume_skip":
                await _kitchen_skip_day_consumption(speak=True)
            elif action == "menu":
                menu = KITCHEN_CURRENT_MENU or _kitchen_load()
                await _kitchen_show_menu(menu)
            elif action == "shopping":
                await _kitchen_show_shopping()
            elif action == "shopping_overlay":
                menu = KITCHEN_CURRENT_MENU
                if menu is None or str(menu.get("date") or "") != _kitchen_today():
                    menu = _kitchen_load()
                overlay_payload = _kitchen_shopping_payload(menu)
            elif action == "shopping_toggle":
                menu = KITCHEN_CURRENT_MENU or _kitchen_load()
                payload = _kitchen_set_shopping_item(menu, value, done_text in {"1", "true", "yes", "on"})
                KITCHEN_CURRENT_STATE = {"screen": "shopping", "date": str(menu.get("date") or ""), "dish": "", "step": 0}
                await kitchen_broadcast(payload)
            elif action == "shopping_toggle_overlay":
                menu = KITCHEN_CURRENT_MENU
                if menu is None or str(menu.get("date") or "") != _kitchen_today():
                    menu = _kitchen_load()
                overlay_payload = _kitchen_set_shopping_item(menu, value, done_text in {"1", "true", "yes", "on"})
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
                if timer.kind != "standalone":
                    delivered, recipe = await _kitchen_open_recipe_by_name(timer.dish, timer.step)
                    if recipe is None:
                        raise KitchenMenuError(f"recipe not found: {timer.dish}")
            elif action == "audio_ack":
                _kitchen_clear_audio(event_id=value)
            elif action == "timer_start":
                seconds = int(seconds_text) if seconds_text else None
                _kitchen_timer_start(seconds)
            elif action == "timer_standalone_start":
                _kitchen_timer_start_standalone(int(seconds_text or "300"))
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
            if overlay_payload is not None:
                payload["overlay"] = overlay_payload
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


def _kitchen_extra_state() -> dict[str, Any]:
    """Legacy R20-R35 extra-menu state, kept only for one-time migration."""
    try:
        raw = json.loads(KITCHEN_TODAY_EXTRA_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _kitchen_markdown_scalar(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _kitchen_recipe_cook_steps(recipe: dict[str, Any]) -> tuple[list[str], list[dict[str, Any] | None]]:
    steps = list(recipe.get("cook_steps") or [])
    timers = list(recipe.get("cook_step_timers") or [])
    if not steps:
        raw_steps = list(recipe.get("steps") or [])
        kinds = list(recipe.get("step_kinds") or [])
        raw_timers = list(recipe.get("step_timers") or [])
        for idx, step in enumerate(raw_steps):
            if idx < len(kinds) and kinds[idx] == "prep":
                continue
            steps.append(str(step))
            timers.append(raw_timers[idx] if idx < len(raw_timers) else None)
    while len(timers) < len(steps):
        timers.append(None)
    return [str(x).strip() for x in steps if str(x).strip()], timers[:len(steps)]


def _kitchen_serialize_menu(menu: dict[str, Any]) -> str:
    """Serialize the authoritative Today Menu back to Obsidian Kitchen Schema v1."""
    date_text = str(menu.get("date") or _kitchen_today())
    items = [dict(x) if isinstance(x, dict) else {"name": str(x)} for x in (menu.get("items") or [])]
    recipes = [ensure_recipe_prep_first(dict(x)) for x in (menu.get("recipes") or []) if isinstance(x, dict)]
    by_name = {str(r.get("name") or "").strip(): r for r in recipes}
    # Ensure item order follows the visible Today's Menu and references only recipes that exist.
    clean_items = []
    for idx, item in enumerate(items):
        name = str(item.get("name") or "").strip()
        if not name or name not in by_name or name == "白米饭":
            continue
        clean_items.append({
            "name": name,
            "category": str(item.get("category") or "other"),
            "cook_order": idx + 1,
        })
    # If a caller passed recipes without item rows, keep them instead of silently losing them.
    known = {x["name"] for x in clean_items}
    for recipe in recipes:
        name = str(recipe.get("name") or "").strip()
        if name and name != "白米饭" and name not in known:
            clean_items.append({"name": name, "category": "other", "cook_order": len(clean_items) + 1})
            known.add(name)

    lines = ["---", "type: dinner-menu", "schema: kitchen-menu-v1", f"date: {date_text}"]
    weekday = str(menu.get("weekday") or "").strip()
    if weekday:
        lines.append(f"weekday: {_kitchen_markdown_scalar(weekday)}")
    servings = menu.get("servings")
    if servings not in (None, ""):
        lines.append(f"servings: {servings}")
    style = str(menu.get("style") or "").strip()
    if style:
        lines.append(f"style: {_kitchen_markdown_scalar(style)}")
    estimated = menu.get("estimated_minutes")
    if estimated not in (None, ""):
        lines.append(f"estimated_minutes: {estimated}")
    status = str(menu.get("status") or "planned").strip() or "planned"
    lines.append(f"status: {_kitchen_markdown_scalar(status)}")
    lines.append("menu:")
    for item in clean_items:
        lines.extend([
            f"  - name: {_kitchen_markdown_scalar(item['name'])}",
            f"    category: {_kitchen_markdown_scalar(item['category'])}",
            f"    cook_order: {item['cook_order']}",
        ])
    lines.extend(["---", "", "## 🛒 一、超市购物单"])
    shopping = menu.get("shopping") or []
    if shopping:
        for group in shopping:
            if not isinstance(group, dict):
                continue
            title = str(group.get("name") or "建议购买").strip()
            lines.extend([f"**{title}**"])
            for item in group.get("items") or []:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
            lines.append("")
    else:
        lines.append("> 暂无")

    lines.extend(["", "## 📋 二、今晚菜单"])
    for idx, item in enumerate(clean_items, 1):
        lines.append(f"{idx}. **{item['name']}**")

    lines.extend(["", "## 🔪 三、统一备菜"])
    for idx, item in enumerate(clean_items, 1):
        recipe = by_name[item["name"]]
        prep = [str(x).strip() for x in (recipe.get("prep_items") or []) if str(x).strip()]
        if prep:
            lines.append(f"### {idx}. {item['name']}")
            lines.extend(f"- {x}" for x in prep)
            lines.append("")

    lines.extend(["", "## 🍳 四、烹饪步骤"])
    for idx, item in enumerate(clean_items, 1):
        recipe = by_name[item["name"]]
        name = item["name"]
        lines.append(f"### {idx}. {name}")
        type_label = str(recipe.get("type_label") or "").strip()
        estimated_text = str(recipe.get("estimated_text") or "").strip()
        if type_label:
            lines.append(f"**类型**：{type_label}")
        if estimated_text:
            lines.append(f"**预计用时**：{estimated_text}")
        ingredients = [str(x).strip() for x in (recipe.get("ingredients") or []) if str(x).strip()]
        seasoning = [str(x).strip() for x in (recipe.get("seasoning") or []) if str(x).strip()]
        prep = [str(x).strip() for x in (recipe.get("prep_items") or []) if str(x).strip()]
        key_points = [str(x).strip() for x in (recipe.get("key_points") or []) if str(x).strip()]
        lines.append("#### 食材")
        lines.extend(f"- {x}" for x in ingredients) if ingredients else lines.append("- 无需额外食材")
        lines.append("#### 调味")
        lines.extend(f"- {x}" for x in seasoning) if seasoning else lines.append("- 按需调味")
        if prep:
            lines.append("#### 备菜")
            lines.extend(f"- {x}" for x in prep)
        lines.append("#### 做法")
        cook_steps, cook_timers = _kitchen_recipe_cook_steps(recipe)
        for step_idx, step in enumerate(cook_steps, 1):
            lines.append(f"{step_idx}. {step}")
            hint = cook_timers[step_idx - 1] if step_idx - 1 < len(cook_timers) else None
            if isinstance(hint, dict) and str(hint.get("source") or "") == "explicit" and str(hint.get("label") or "").strip():
                lines.append(f"   - ⏱️ 计时：{str(hint.get('label')).strip()}")
        lines.append("#### 关键点")
        lines.extend(f"- {x}" for x in key_points) if key_points else lines.append("- 按步骤完成即可。")
        lines.append("")

    lines.extend(["", "## ⏱️ 五、省事操作时间线"])
    timeline = [str(x).strip() for x in (menu.get("timeline") or []) if str(x).strip()]
    if timeline:
        lines.extend(f"{idx}. {text}" for idx, text in enumerate(timeline, 1))
    else:
        for idx, item in enumerate(clean_items, 1):
            lines.append(f"{idx}. {item['name']}")
    return "\n".join(lines).rstrip() + "\n"


def _kitchen_write_menu_to_obsidian(menu: dict[str, Any]) -> Path:
    """Atomically write the one authoritative Today Menu file in Obsidian."""
    KITCHEN_MENU_DIR.mkdir(parents=True, exist_ok=True)
    date_text = str(menu.get("date") or _kitchen_today())
    target = KITCHEN_MENU_DIR / f"{date_text}.md"
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    text = _kitchen_serialize_menu(menu)
    # Never replace the Obsidian source with a document the current parser cannot read back.
    parse_kitchen_menu(text, source=str(target))
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return target


def _kitchen_migrate_legacy_extras_once() -> None:
    """Move legacy Gateway extra-recipes into Obsidian, then delete the second store."""
    if not KITCHEN_TODAY_EXTRA_FILE.exists():
        return
    state = _kitchen_extra_state()
    if not state:
        try:
            KITCHEN_TODAY_EXTRA_FILE.unlink()
        except OSError:
            pass
        return
    migrated = 0
    for date_text, extras in state.items():
        if not isinstance(extras, list):
            continue
        try:
            menu = load_kitchen_menu(KITCHEN_MENU_DIR, str(date_text))
        except (KitchenMenuError, OSError):
            menu = _kitchen_empty_today_menu(date_text=str(date_text))
        existing = {str(r.get("name") or "") for r in (menu.get("recipes") or [])}
        for raw in extras:
            if not isinstance(raw, dict) or not isinstance(raw.get("recipe"), dict):
                continue
            recipe = ensure_recipe_prep_first(dict(raw["recipe"]))
            name = str(recipe.get("name") or "").strip()
            if not name or name == "白米饭" or name in existing:
                continue
            menu.setdefault("recipes", []).append(recipe)
            menu.setdefault("items", []).append({"name": name, "category": str(raw.get("category") or "other"), "cook_order": len(menu.get("items") or []) + 1})
            existing.add(name)
            migrated += 1
        _kitchen_write_menu_to_obsidian(menu)
    try:
        KITCHEN_TODAY_EXTRA_FILE.unlink()
    except OSError as exc:
        print(f"[KITCHEN-WARN] legacy extra-state cleanup failed: {exc}")
    print(f"[KITCHEN] migrated legacy Today Menu extras into Obsidian count={migrated}")


def _kitchen_private_section(text: str, fragment: str) -> str:
    m = re.search(rf"^##\s+[^\n]*{re.escape(fragment)}[^\n]*\n(?P<body>.*?)(?=^##\s+|\Z)", text, re.M | re.S)
    return m.group("body").strip() if m else ""

def _kitchen_private_first_section(text: str, fragments: list[str]) -> str:
    for fragment in fragments:
        body = _kitchen_private_section(text, fragment)
        if body:
            return body
    return ""

def _kitchen_private_bullets(text: str) -> list[str]:
    return [re.sub(r"^\s*[-*+]\s+", "", line).strip() for line in text.splitlines() if re.match(r"^\s*[-*+]\s+", line) and "计时" not in line]

def _kitchen_private_steps(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for line in text.splitlines():
        m = re.match(r"^\s*\d+[.)、]\s*(.+)$", line)
        if m:
            if current:
                out.append(current.strip())
            current = m.group(1).strip()
            continue
        stripped = line.strip()
        if current and stripped and not stripped.startswith("- ⏱") and not stripped.startswith("- ⏱️"):
            current += " " + stripped
    if current:
        out.append(current.strip())
    return out

def _kitchen_parse_private_recipe(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    name = ""
    # Prefer explicit Obsidian/YAML metadata when available.
    for key in ("name", "title"):
        m = re.search(rf"^{key}:\s*(.+?)\s*$", text, re.M | re.I)
        if not m:
            continue
        raw = m.group(1).strip()
        try:
            name = str(json.loads(raw))
        except Exception:
            name = raw.strip("'\"")
        if name:
            break

    # Accept KitchenTerminal's heading and ordinary Obsidian '# 菜名'.
    if not name:
        m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
        if m:
            name = m.group(1).strip()
            name = re.sub(r"^[🍳🥘🍲🥗🍜🍛🍚\s]+", "", name).strip()
            name = re.sub(r"^(?:私房菜|菜谱)\s*[·：:\-|]\s*", "", name).strip()
    if not name:
        name = path.stem.strip()

    ingredients_text = _kitchen_private_first_section(text, ["食材", "材料", "主料", "原料"])
    seasoning_text = _kitchen_private_first_section(text, ["调味", "调料", "佐料", "辅料"])
    prep_text = _kitchen_private_first_section(text, ["备菜", "准备", "预处理"])
    steps_text = _kitchen_private_first_section(text, ["做法", "烹饪步骤", "制作步骤", "步骤", "制作方法", "烹饪方法"])
    key_text = _kitchen_private_first_section(text, ["关键点", "注意事项", "小贴士", "技巧", "要点"])

    steps = _kitchen_private_steps(steps_text) if steps_text else []
    if not steps and steps_text:
        # Some Obsidian recipes use bullet steps rather than numbered steps.
        steps = _kitchen_private_bullets(steps_text)
    if not steps and steps_text:
        for block in re.split(r"\n\s*\n|\n", steps_text):
            line = re.sub(r"^\s*[-*+>]\s*", "", block).strip()
            if line and not line.startswith("#") and "计时" not in line:
                steps.append(line)
    if not steps:
        # Older personal notes may simply put a numbered method list in the body.
        steps = _kitchen_private_steps(text)

    if not name or not steps:
        return None

    recipe = {
        "name": name,
        "type_label": "私房菜",
        "estimated_text": "",
        "ingredients": _kitchen_private_bullets(ingredients_text),
        "seasoning": _kitchen_private_bullets(seasoning_text),
        "prep_items": _kitchen_private_bullets(prep_text),
        "steps": steps,
        "step_timers": [None] * len(steps),
        "key_points": _kitchen_private_bullets(key_text),
    }
    return ensure_recipe_prep_first(recipe)


def _kitchen_private_recipe_dirs() -> list[Path]:
    """Find nearby Obsidian 私房菜 folders without requiring one fixed layout."""
    candidates: list[Path] = [
        KITCHEN_PRIVATE_RECIPE_DIR,
        KITCHEN_MENU_DIR / "私房菜",
        KITCHEN_MENU_DIR.parent / "私房菜",
    ]

    roots: list[Path] = []
    for root in (KITCHEN_MENU_DIR.parent, KITCHEN_MENU_DIR.parent.parent):
        if root.exists() and root not in roots:
            roots.append(root)
    for root in roots:
        try:
            candidates.extend(root.glob("*/私房菜"))
            if root == KITCHEN_MENU_DIR.parent:
                candidates.extend(root.glob("*/*/私房菜"))
        except OSError:
            pass

    result: list[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        folder = raw.expanduser()
        try:
            key = str(folder.resolve())
        except OSError:
            key = str(folder)
        if key in seen or not folder.is_dir():
            continue
        seen.add(key)
        result.append(folder)
    return result

def _kitchen_library_id(source: str, name: str) -> str:
    return hashlib.sha1((str(source) + "\\n" + str(name)).encode("utf-8")).hexdigest()[:18]


def _kitchen_private_recipe_library() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    paths: list[Path] = []
    seen_paths: set[str] = set()
    for folder in _kitchen_private_recipe_dirs():
        try:
            for path in folder.rglob("*.md"):
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                paths.append(path)
        except OSError:
            continue

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    for path in sorted(paths, key=_mtime, reverse=True)[:240]:
        recipe = _kitchen_parse_private_recipe(path)
        if not recipe:
            continue
        name = str(recipe.get("name") or "").strip()
        if not name:
            continue
        entries.append({
            "id": _kitchen_library_id(str(path), name),
            "name": name,
            "source_kind": "private",
            "source_label": "私房菜",
            "source": str(path),
            "recipe": recipe,
        })
    return entries

def _kitchen_recipe_library() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = _kitchen_private_recipe_library()
    seen: set[str] = set()
    if KITCHEN_MENU_DIR.exists():
        paths = sorted(KITCHEN_MENU_DIR.glob("*.md"), key=lambda x: x.name, reverse=True)[:120]
        for path in paths:
            try:
                menu = load_kitchen_menu(KITCHEN_MENU_DIR, path.stem)
            except (KitchenMenuError, OSError):
                continue
            for raw in menu.get("recipes") or []:
                recipe = ensure_recipe_prep_first(dict(raw))
                name = str(recipe.get("name") or "").strip()
                key = name.lower()
                if not name or key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "id": _kitchen_library_id(str(path), name),
                    "name": name,
                    "source_kind": "history",
                    "source_label": "历史菜谱",
                    "source": str(path),
                    "recipe": recipe,
                })
    return entries


def _kitchen_library_public(entry: dict[str, Any], inventory_names: list[str] | None = None) -> dict[str, Any]:
    recipe = entry.get("recipe") or {}
    ingredient_text = " ".join(
        str(x) for x in ((recipe.get("ingredients") or []) + (recipe.get("seasoning") or []))
    )
    matches = [name for name in (inventory_names or []) if name and name in ingredient_text]
    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "source_kind": str(entry.get("source_kind") or "history"),
        "source_label": str(entry.get("source_label") or "菜谱"),
        "type_label": str(recipe.get("type_label") or ""),
        "estimated_text": str(recipe.get("estimated_text") or ""),
        "inventory_matches": matches,
        "recommendation_reason": str(entry.get("recommendation_reason") or ""),
    }


def _kitchen_empty_today_menu(date_text: str | None = None) -> dict[str, Any]:
    return {
        "date": str(date_text or _kitchen_today()),
        "weekday": "",
        "servings": 3,
        "estimated_minutes": None,
        "items": [],
        "recipes": [],
        "shopping": [],
        "timeline": [],
        "source": "gateway-empty-today",
    }


def _kitchen_menu_or_empty_today() -> dict[str, Any]:
    _kitchen_migrate_legacy_extras_once()
    menu = KITCHEN_CURRENT_MENU
    if menu is not None and str(menu.get("date") or "") == _kitchen_today():
        return menu
    try:
        return _kitchen_load()
    except (KitchenMenuError, OSError):
        return _kitchen_empty_today_menu()


def _kitchen_inventory_public_items() -> list[dict[str, Any]]:
    snap = _food_snapshot()
    result: list[dict[str, Any]] = []
    for item in snap.get("items") or []:
        name = str(item.get("name") or "").strip()
        unit = str(item.get("unit") or "").strip()
        quantity = float(item.get("quantity") or 0)
        if not name or unit == "状态" or quantity <= 0:
            continue
        result.append({
            "name": name,
            "category": str(item.get("category") or ""),
            "quantity": int(quantity) if quantity.is_integer() else round(quantity, 2),
            "unit": unit,
        })
    return result


def _kitchen_picker_payload(menu: dict[str, Any], *, selected: list[str] | None = None, recommended: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # Initial picker load is deliberately light: no historical recipe scan and
    # no inventory-to-recipe matching until the user chooses ingredients.
    private = [_kitchen_library_public(e) for e in _kitchen_private_recipe_library()]
    return {
        "type": "kitchen.show_picker",
        "eyebrow": "小K · 今日菜谱",
        "title": "选择今日菜谱",
        "message": "先选今天想用的食材，再看库存推荐菜；也可以直接从私房菜选择",
        "today_names": [str(x.get("name") or "") if isinstance(x, dict) else str(x) for x in (menu.get("items") or [])],
        "inventory_items": _kitchen_inventory_public_items(),
        "inventory_selected": list(selected or []),
        "recommended": list(recommended or []),
        "private_recipes": private[:120],
        "private_recipe_dirs": [str(x) for x in _kitchen_private_recipe_dirs()],
        "active_source": "inventory",
        "footer": "今日菜谱是每天真正的做饭入口",
    }


def _kitchen_compact_generated_steps(steps: list[str], max_steps: int = 7) -> list[str]:
    """Compact over-fragmented AI cooking steps without dropping content.

    This is intentionally conservative: only adjacent text steps are merged.
    Generated recommendations do not carry per-step timers, so merging here is
    safe and keeps the kitchen UI at a practical 4-7 stage granularity.
    """
    clean = [str(x).strip() for x in steps if str(x).strip()]
    if len(clean) <= max_steps:
        return clean
    while len(clean) > max_steps:
        # Merge the shortest adjacent pair first; micro-steps naturally collapse
        # before longer stage descriptions.
        best_i = min(range(len(clean)-1), key=lambda i: len(clean[i]) + len(clean[i+1]))
        a = clean[best_i].rstrip('。；;，, ')
        b = clean[best_i+1].lstrip('。；;，, ')
        clean[best_i:best_i+2] = [a + '；' + b]
    return clean


async def _kitchen_inventory_recommend_entries(selected: list[str]) -> list[dict[str, Any]]:
    selected_clean: list[str] = []
    seen: set[str] = set()
    for raw in selected:
        name = str(raw or "").strip()
        if name and name not in seen:
            selected_clean.append(name)
            seen.add(name)
    if not selected_clean:
        raise KitchenMenuError("请先选择至少一种食材")

    snap = _food_snapshot()
    available_bits: list[str] = []
    for item in snap.get("items") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        unit = str(item.get("unit") or "").strip()
        if unit == "状态":
            status = str(item.get("status") or "一般")
            available_bits.append(f"{name}({status})")
        else:
            q = item.get("quantity")
            available_bits.append(f"{name}({q}{unit})")

    prompt = f"""你是小K家庭厨房的库存推荐模块。默认家庭3人。
用户从现有库存里主动选择了这些今天想优先使用的食材：{ '、'.join(selected_clean) }。
当前家里可用食材/佐料概况：{ '、'.join(available_bits[:80]) }。

请推荐4到6道适合今天做的家常菜。要求：
1. 优先消耗用户主动选择的食材，可搭配当前库存里的其他食材；
2. 不要把“白米饭”作为一道菜，白米饭只视为默认配饭主食；
3. 菜名和做法要具体、真实、适合3人家庭；
4. 每道菜给出完整可执行菜谱，后续会直接加入“小K今日菜谱”；
5. prep_items 只放不需要开火的准备工作，例如清洗、切配、泡发、解冻、腌制、调汁、称量；焯水、预热、烧水/烧开水都属于正式烹饪，必须放进 steps，禁止放入 prep_items；
6. 正式烹饪步骤控制在4到7步，每一步必须代表一个完整烹饪阶段，不要把“下油、放葱、翻炒两下、加盐”拆成四个独立步骤；
7. 同一锅、同一阶段内连续发生的动作合并写在同一步里，用“；”连接，只有火候/等待/转锅等明显阶段变化才另起一步；
8. 不要为了凑步骤数增加无意义步骤；
9. 只输出JSON，不要Markdown，不要解释。

JSON格式必须严格为：
{{
  "recipes": [
    {{
      "name": "菜名",
      "reason": "为什么适合当前所选食材，20字以内",
      "type_label": "主菜/蔬菜/汤/其他",
      "estimated_text": "约20分钟",
      "ingredients": ["食材及家庭用量"],
      "seasoning": ["调味及用量"],
      "prep_items": ["所有不需要开火的准备事项"],
      "steps": ["正式烹饪步骤1", "正式烹饪步骤2"],
      "key_points": ["关键提醒"]
    }}
  ]
}}"""
    headers = {"Content-Type": "application/json"}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"
    kitchen_user = os.getenv("HOMEAI_KITCHEN_OPENCLAW_USER", "home-ai-kitchen:main").strip() or "home-ai-kitchen:main"
    payload = {
        "model": OPENCLAW_MODEL,
        "user": kitchen_user,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        async with _openclaw_http_client(OPENCLAW_KITCHEN_TIMEOUT_SEC) as client:
            response = await _post_with_retry(
                client,
                f"{OPENCLAW_BASE_URL}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise KitchenMenuError("小K没有返回推荐菜谱")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        parsed = _extract_json_object(str(content))
        raw_recipes = parsed.get("recipes") or []
    except KitchenMenuError:
        raise
    except Exception as exc:
        raise KitchenMenuError(f"库存推荐暂时不可用：{exc}") from exc

    entries: list[dict[str, Any]] = []
    for raw in raw_recipes[:6]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name == "白米饭":
            continue
        steps = _kitchen_compact_generated_steps([str(x).strip() for x in (raw.get("steps") or []) if str(x).strip()])
        prep_items = [str(x).strip() for x in (raw.get("prep_items") or []) if str(x).strip()]
        if not steps:
            continue
        recipe = ensure_recipe_prep_first({
            "name": name,
            "type_label": str(raw.get("type_label") or "家常菜"),
            "estimated_text": str(raw.get("estimated_text") or ""),
            "ingredients": [str(x).strip() for x in (raw.get("ingredients") or []) if str(x).strip()],
            "seasoning": [str(x).strip() for x in (raw.get("seasoning") or []) if str(x).strip()],
            "prep_items": prep_items,
            "steps": steps,
            "step_timers": [None] * len(steps),
            "key_points": [str(x).strip() for x in (raw.get("key_points") or []) if str(x).strip()],
        })
        rid = "air-" + hashlib.sha1(("|".join(selected_clean) + "\n" + name).encode("utf-8")).hexdigest()[:18]
        entry = {
            "id": rid,
            "name": name,
            "source_kind": "inventory_ai",
            "source_label": "库存推荐",
            "source": "openclaw:inventory-recommend",
            "recipe": recipe,
            "recommendation_reason": str(raw.get("reason") or "").strip(),
        }
        KITCHEN_PICKER_RECOMMENDATIONS[rid] = entry
        entries.append(entry)
    if not entries:
        raise KitchenMenuError("没有生成可用的库存推荐菜谱")
    # Keep temporary recommendation cache bounded.
    if len(KITCHEN_PICKER_RECOMMENDATIONS) > 80:
        for key in list(KITCHEN_PICKER_RECOMMENDATIONS)[:-60]:
            KITCHEN_PICKER_RECOMMENDATIONS.pop(key, None)
    return entries


async def _kitchen_inventory_recommend_payload(menu: dict[str, Any], selected: list[str]) -> dict[str, Any]:
    selected_clean = []
    seen: set[str] = set()
    for raw in selected:
        name = str(raw or "").strip()
        if name and name not in seen:
            selected_clean.append(name)
            seen.add(name)
    if not selected_clean:
        raise KitchenMenuError("请先选择至少一种食材")
    entries = await _kitchen_inventory_recommend_entries(selected_clean)
    public = [_kitchen_library_public(e, selected_clean) for e in entries]
    return _kitchen_picker_payload(menu, selected=selected_clean, recommended=public)


def _kitchen_find_library_entry(recipe_id: str) -> dict[str, Any] | None:
    cached = KITCHEN_PICKER_RECOMMENDATIONS.get(str(recipe_id))
    if cached is not None:
        return cached
    for entry in _kitchen_recipe_library():
        if str(entry.get("id") or "") == str(recipe_id):
            return entry
    return None


def _kitchen_add_library_recipe_to_today(recipe_id: str) -> dict[str, Any]:
    global KITCHEN_CURRENT_MENU
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_menu_or_empty_today()
    entry = _kitchen_find_library_entry(recipe_id)
    if entry is None:
        raise KitchenMenuError("recipe library item not found")
    recipe = ensure_recipe_prep_first(dict(entry.get("recipe") or {}))
    name = str(recipe.get("name") or "").strip()
    if not name:
        raise KitchenMenuError("recipe name is empty")
    if name == "白米饭":
        raise KitchenMenuError("白米饭是默认主食，不加入今日菜谱")
    if recipe_for(menu, name) is not None:
        return menu
    menu.setdefault("recipes", []).append(recipe)
    menu.setdefault("items", []).append({
        "name": name,
        "category": str((entry.get("recipe") or {}).get("type_label") or "other"),
        "cook_order": len(menu.get("items") or []) + 1,
    })
    path = _kitchen_write_menu_to_obsidian(menu)
    KITCHEN_CURRENT_MENU = load_kitchen_menu(KITCHEN_MENU_DIR, str(menu.get("date") or _kitchen_today()))
    print(f"[KITCHEN] Today Menu added to Obsidian dish={name!r} path={path}")
    return KITCHEN_CURRENT_MENU


def _kitchen_remove_recipe_from_today(name: str) -> dict[str, Any]:
    global KITCHEN_CURRENT_MENU
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_menu_or_empty_today()
    target = str(name or "").strip()
    if not target:
        raise KitchenMenuError("recipe name is empty")
    recipe_before = len(menu.get("recipes") or [])
    item_before = len(menu.get("items") or [])
    menu["recipes"] = [r for r in (menu.get("recipes") or []) if str((r or {}).get("name") or "") != target]
    menu["items"] = [x for x in (menu.get("items") or []) if str((x or {}).get("name") if isinstance(x, dict) else x) != target]
    if len(menu["recipes"]) == recipe_before and len(menu["items"]) == item_before:
        # Idempotent delete: the dish is already absent, so the requested end state is satisfied.
        # This also protects against duplicate taps/retries after a successful Obsidian rewrite.
        KITCHEN_CURRENT_MENU = menu
        print(f"[KITCHEN] Today Menu remove already absent dish={target!r}")
        return menu
    for idx, item in enumerate(menu["items"], 1):
        if isinstance(item, dict):
            item["cook_order"] = idx
    path = _kitchen_write_menu_to_obsidian(menu)
    date_text = str(menu.get("date") or _kitchen_today())
    if target in KITCHEN_RECIPE_PROGRESS.get(date_text, {}):
        KITCHEN_RECIPE_PROGRESS[date_text].pop(target, None)
        if not KITCHEN_RECIPE_PROGRESS[date_text]:
            KITCHEN_RECIPE_PROGRESS.pop(date_text, None)
        save_kitchen_progress()
    # Prep task ids include the dish name; rebuild/trim valid ids from the updated menu.
    if date_text in KITCHEN_PREP_CHECKED:
        valid = set()
        for recipe in menu.get("recipes") or []:
            dish = str(recipe.get("name") or "")
            tasks = [str(x).strip() for x in (recipe.get("prep_items") or []) if str(x).strip()]
            if not tasks:
                tasks = ["确认食材、调味和所需厨具已备齐。"]
            for idx, text in enumerate(tasks):
                valid.add(_kitchen_prep_task_id(date_text, dish, idx, text))
        KITCHEN_PREP_CHECKED[date_text].intersection_update(valid)
        if not KITCHEN_PREP_CHECKED[date_text]:
            KITCHEN_PREP_CHECKED.pop(date_text, None)
        save_kitchen_prep_state()
    KITCHEN_CURRENT_MENU = load_kitchen_menu(KITCHEN_MENU_DIR, date_text)
    print(f"[KITCHEN] Today Menu removed from Obsidian dish={target!r} path={path}")
    return KITCHEN_CURRENT_MENU


def _kitchen_load(date_text: str | None = None) -> dict[str, Any]:
    global KITCHEN_CURRENT_MENU
    _kitchen_migrate_legacy_extras_once()
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



def _kitchen_dashboard_payload(menu: dict[str, Any]) -> dict[str, Any]:
    """One-screen landscape cooking cockpit.

    The cockpit is deliberately a view over existing sources of truth:
    Obsidian menu + progress + prep state + Gateway timers.  It does not
    create a second cooking state machine.
    """
    date_text = str(menu.get("date") or _kitchen_today())
    menu_payload = _kitchen_menu_payload(menu)
    items = list(menu_payload.get("items") or [])
    names = [str(x.get("name") or "") for x in items if str(x.get("name") or "")]

    requested_dish = str(KITCHEN_CURRENT_STATE.get("dish") or "").strip()
    focus = requested_dish if requested_dish in names else ""
    if not focus:
        for item in items:
            if bool(item.get("has_progress")) and int(item.get("progress_step") or 0) > 0:
                focus = str(item.get("name") or "")
                break
    if not focus and names:
        focus = names[0]

    current: dict[str, Any] | None = None
    next_item: dict[str, Any] | None = None
    if focus:
        recipe = recipe_for(menu, focus)
        if recipe is not None:
            steps = list(recipe.get("steps") or [])
            step, _ = _kitchen_progress_get(date_text, focus, len(steps)) if steps else (0, False)
            # When returning from a recipe, preserve the exact step that was on screen.
            if requested_dish == focus and str(KITCHEN_CURRENT_STATE.get("screen") or "") == "recipe":
                step = max(0, min(int(KITCHEN_CURRENT_STATE.get("step") or 0), max(0, len(steps) - 1)))
            current = {
                "name": focus,
                "step": step,
                "total_steps": len(steps),
                "step_text": str(steps[step]) if steps and 0 <= step < len(steps) else "准备开始这道菜",
                "step_kind": str((recipe.get("step_kinds") or [])[step] if step < len(recipe.get("step_kinds") or []) else "cook"),
                "timer_hint": _kitchen_step_timer_hint(recipe, step),
            }
            try:
                idx = names.index(focus)
            except ValueError:
                idx = -1
            if idx >= 0 and idx + 1 < len(names):
                next_name = names[idx + 1]
                next_recipe = recipe_for(menu, next_name)
                next_steps = list((next_recipe or {}).get("steps") or [])
                next_step, next_progress = _kitchen_progress_get(date_text, next_name, len(next_steps)) if next_steps else (0, False)
                next_item = {
                    "name": next_name,
                    "step": next_step,
                    "total_steps": len(next_steps),
                    "has_progress": next_progress,
                }

    prep = _kitchen_prep_payload(menu)
    pending: list[dict[str, Any]] = []
    for group in prep.get("groups") or []:
        for task in group.get("tasks") or []:
            if not bool(task.get("done")):
                pending.append({
                    "id": str(task.get("id") or ""),
                    "dish": str(group.get("dish") or ""),
                    "text": str(task.get("text") or ""),
                })
            if len(pending) >= 4:
                break
        if len(pending) >= 4:
            break

    return {
        "type": "kitchen.show_dashboard",
        "eyebrow": f"{menu.get('date','')} · 烹饪中",
        "title": "厨房中台",
        "message": "一屏掌控当前菜、后台计时和下一步",
        "items": items,
        "current": current,
        "next": next_item,
        "pending": pending,
        "prep_completed": int(prep.get("completed") or 0),
        "prep_total": int(prep.get("total") or 0),
        "prep_all_done": bool(prep.get("all_done")),
        "footer": "厨房中台",
    }



def _kitchen_consumption_match_text(value: str) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:kg|g|克|公斤|斤|两|份|个|颗|根|把|盒|瓶|包|袋|杯|块|片|只|枚)", "", text, flags=re.I)
    text = re.sub(r"[\s·•，,、:：/\\-]+", "", text)
    for token in ("新鲜", "冷冻", "冷藏", "净", "约", "适量", "少许", "去皮", "去骨", "切块", "切片", "切段"):
        text = text.replace(token, "")
    return text


def _kitchen_consumption_number(recipe_text: str, unit: str, available: float) -> float:
    text = str(recipe_text or "")
    target_unit = str(unit or "")
    m = re.search(rf"(\d+(?:\.\d+)?)\s*{re.escape(target_unit)}", text)
    if m:
        try:
            return min(float(available), max(0.0, float(m.group(1))))
        except ValueError:
            pass
    chinese = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    m2 = re.search(rf"([半一二两三四五六七八九十])\s*{re.escape(target_unit)}", text)
    if m2:
        return min(float(available), float(chinese.get(m2.group(1), 1)))
    return min(float(available), 1.0)


def _kitchen_recipe_consumption_payload(menu: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    snap = _food_snapshot()
    inventory = [x for x in (snap.get("items") or []) if str(x.get("unit") or "") != "状态" and float(x.get("quantity") or 0) > 0]
    recipe_ingredients = [str(x).strip() for x in (recipe.get("ingredients") or []) if str(x).strip()]
    candidates: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for inv in inventory:
        name = str(inv.get("name") or "").strip()
        if not name or name in used_names:
            continue
        name_key = _kitchen_consumption_match_text(name)
        best = ""
        for raw in recipe_ingredients:
            ing_key = _kitchen_consumption_match_text(raw)
            if not ing_key or not name_key:
                continue
            if name_key in ing_key or ing_key in name_key:
                best = raw
                break
        if not best:
            continue
        available = float(inv.get("quantity") or 0)
        unit = str(inv.get("unit") or "份")
        suggested = _kitchen_consumption_number(best, unit, available)
        if unit != "份":
            suggested = float(int(suggested)) if float(suggested).is_integer() else suggested
        candidates.append({
            "name": name,
            "category": str(inv.get("category") or "食材"),
            "unit": unit,
            "available": int(available) if available.is_integer() else round(available, 2),
            "suggested": int(suggested) if float(suggested).is_integer() else round(float(suggested), 2),
            "recipe_text": best,
        })
        used_names.add(name)
    return {
        "type": "kitchen.show_consumption",
        "eyebrow": f"{menu.get('date','')} · 今日菜谱",
        "title": "登记消耗",
        "dish": str(recipe.get("name") or ""),
        "items": candidates,
        "message": "按实际用量登记，确认后会直接扣减食材库存。",
        "footer": "只扣你确认的数量",
    }



def _kitchen_day_consumption_payload(menu: dict[str, Any]) -> dict[str, Any]:
    aggregated: dict[str, dict[str, Any]] = {}
    for recipe in menu.get("recipes") or []:
        if not isinstance(recipe, dict):
            continue
        dish_name = str(recipe.get("name") or "").strip()
        payload = _kitchen_recipe_consumption_payload(menu, recipe)
        for raw in payload.get("items") or []:
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            row = aggregated.get(name)
            if row is None:
                row = {
                    "name": name,
                    "category": str(raw.get("category") or "食材"),
                    "unit": str(raw.get("unit") or "份"),
                    "available": raw.get("available") or 0,
                    "suggested": 0.0,
                    "recipe_texts": [],
                    "dishes": [],
                }
                aggregated[name] = row
            if dish_name and dish_name not in row["dishes"]:
                row["dishes"].append(dish_name)
            text = str(raw.get("recipe_text") or "").strip()
            if text and text not in row["recipe_texts"]:
                row["recipe_texts"].append(text)
            try:
                row["suggested"] = float(row.get("suggested") or 0) + float(raw.get("suggested") or 0)
            except (TypeError, ValueError):
                pass
    items: list[dict[str, Any]] = []
    for row in aggregated.values():
        try:
            available = float(row.get("available") or 0)
        except (TypeError, ValueError):
            available = 0.0
        suggested = min(available, max(0.0, float(row.get("suggested") or 0)))
        unit = str(row.get("unit") or "份")
        if unit != "份" and float(suggested).is_integer():
            suggested_out: int | float = int(suggested)
        else:
            suggested_out = round(suggested, 2)
        if float(available).is_integer():
            available_out: int | float = int(available)
        else:
            available_out = round(available, 2)
        items.append({
            "name": row["name"],
            "category": row["category"],
            "unit": unit,
            "available": available_out,
            "suggested": suggested_out,
            "recipe_text": "；".join(row.get("recipe_texts") or []),
            "dishes": row.get("dishes") or [],
        })
    items.sort(key=lambda x: (str(x.get("category") or ""), str(x.get("name") or "")))
    return {
        "type": "kitchen.show_consumption",
        "scope": "day",
        "eyebrow": f"{menu.get('date','')} · 今日收尾",
        "title": "今日食材结算",
        "dish": "",
        "items": items,
        "message": "核对今天实际用掉的食材和数量，确认后统一扣减库存。",
        "footer": "确认实际消耗后扣减库存，然后继续私房菜收尾",
    }


async def _kitchen_show_save_private_after_consumption(*, speak: bool = False) -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    KITCHEN_CURRENT_STATE = {'screen': 'save_private', 'date': str(menu.get('date') or ''), 'dish': '', 'step': 0}
    delivered = await kitchen_broadcast(_kitchen_save_private_payload(menu))
    if speak:
        await _kitchen_speak('库存消耗已经处理。接下来可以选择要保存到私房菜的菜谱，也可以直接结束。', kind='assistant')
    return delivered


async def _kitchen_commit_day_consumption(rows: list[Any], *, speak: bool = False) -> int:
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    allowed = {str(x.get("name") or "") for x in (_kitchen_day_consumption_payload(menu).get("items") or [])}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        try:
            amount = float(raw.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if not name or name not in allowed or amount <= 0:
            continue
        _food_consume(name, amount)
    return await _kitchen_show_save_private_after_consumption(speak=speak)


async def _kitchen_skip_day_consumption(*, speak: bool = False) -> int:
    return await _kitchen_show_save_private_after_consumption(speak=speak)


async def _kitchen_show_recipe_consumption(dish_name: str = "") -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    dish = str(dish_name or KITCHEN_CURRENT_STATE.get("dish") or "").strip()
    recipe = recipe_for(menu, dish)
    if recipe is None:
        raise KitchenMenuError(f"recipe not found: {dish}")
    KITCHEN_CURRENT_STATE = {
        "screen": "consumption",
        "date": str(menu.get("date") or ""),
        "dish": dish,
        "step": max(0, len(recipe.get("steps") or []) - 1),
    }
    return await kitchen_broadcast(_kitchen_recipe_consumption_payload(menu, recipe))


async def _kitchen_commit_recipe_consumption(rows: list[Any]) -> int:
    menu = KITCHEN_CURRENT_MENU or _kitchen_load()
    dish = str(KITCHEN_CURRENT_STATE.get("dish") or "").strip()
    recipe = recipe_for(menu, dish)
    if recipe is None:
        raise KitchenMenuError(f"recipe not found: {dish}")
    allowed = {str(x.get("name") or "") for x in (_kitchen_recipe_consumption_payload(menu, recipe).get("items") or [])}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        try:
            amount = float(raw.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if not name or name not in allowed or amount <= 0:
            continue
        _food_consume(name, amount)
    return await _kitchen_show_menu(menu)


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
        "step_kind": str((recipe.get("step_kinds") or [])[step] if step < len(recipe.get("step_kinds") or []) else "cook"),
        "timer_hint": timer_hint,
        "key_points": recipe.get("key_points") or [],
        "footer": "语音可说：下一步 / 上一步 / 开始计时 / 还有多久",
    }


def _kitchen_shopping_payload(menu: dict[str, Any]) -> dict[str, Any]:
    groups, _ = _kitchen_shopping_groups_with_state(menu)
    return {
        "type": "kitchen.show_shopping",
        "eyebrow": f"{menu.get('date','')} · 采购",
        "title": "今日采购清单",
        "message": "勾选后自动保存；已有库存会在右侧显示当前数量",
        "groups": groups,
        "footer": "今日采购清单",
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


async def _kitchen_show_dashboard() -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_load()
    payload = _kitchen_dashboard_payload(menu)
    current = payload.get("current") or {}
    KITCHEN_CURRENT_STATE = {
        "screen": "dashboard",
        "date": str(menu.get("date") or ""),
        "dish": str(current.get("name") or ""),
        "step": max(0, int(current.get("step") or 0)),
    }
    return await kitchen_broadcast(payload)


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


async def _kitchen_show_home() -> int:
    global KITCHEN_CURRENT_MENU, KITCHEN_CURRENT_STATE
    # Home's “获取今日菜谱” is a refresh, not a navigation action. Reload the
    # authoritative Obsidian day file and remain on Home so the CTA can switch
    # in-place to “开工烧饭” as soon as today's menu exists.
    try:
        menu = _kitchen_load()
    except KitchenMenuError:
        KITCHEN_CURRENT_MENU = None
        menu = None
    KITCHEN_CURRENT_STATE = {"screen": "idle", "date": str((menu or {}).get("date") or ""), "dish": "", "step": 0}
    return await kitchen_broadcast(_kitchen_idle_payload("今天想做点什么？"))


async def _kitchen_show_picker() -> int:
    global KITCHEN_CURRENT_STATE
    menu = _kitchen_menu_or_empty_today()
    KITCHEN_CURRENT_STATE = {"screen": "picker", "date": str(menu.get("date") or ""), "dish": "", "step": 0}
    return await kitchen_broadcast(_kitchen_picker_payload(menu))


async def _kitchen_show_inventory_recommendations(selected: list[str]) -> int:
    global KITCHEN_CURRENT_STATE
    menu = _kitchen_menu_or_empty_today()
    KITCHEN_CURRENT_STATE = {"screen": "picker", "date": str(menu.get("date") or ""), "dish": "", "step": 0}
    return await kitchen_broadcast(await _kitchen_inventory_recommend_payload(menu, selected))


async def _kitchen_show_prep() -> int:
    global KITCHEN_CURRENT_STATE
    menu = KITCHEN_CURRENT_MENU
    if menu is None or str(menu.get("date") or "") != _kitchen_today():
        menu = _kitchen_load()
    KITCHEN_CURRENT_STATE = {"screen": "prep", "date": str(menu.get("date") or ""), "dish": "", "step": 0}
    return await kitchen_broadcast(_kitchen_prep_payload(menu))


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
                elif session.food_scan_receiving and not session.food_scan_processing:
                    if len(session.food_scan_image) + len(raw) > FOOD_SCAN_MAX_BYTES:
                        session.food_scan_receiving = False
                        session.food_scan_image.clear()
                        await _food_scan_notify(session, {"type": "kitchen.food.scan.error", "request_id": session.food_scan_request_id, "ok": False, "message": "图片太大，请选择 12MB 以内的图片"})
                    else:
                        session.food_scan_image.extend(raw)
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
            elif kind == "kitchen.food.scan.start":
                if session.food_scan_processing:
                    await _food_scan_notify(session, {"type": "kitchen.food.scan.error", "ok": False, "message": "上一张图片还在识别中"})
                    continue
                session.food_scan_request_id = str(msg.get("request_id") or ("fs-" + uuid.uuid4().hex[:12]))[:80]
                session.food_scan_source = str(msg.get("source") or "菜市场").strip()[:24]
                session.food_scan_mime = str(msg.get("mime") or "image/jpeg").strip()[:64]
                session.food_scan_image.clear()
                session.food_scan_receiving = True
                await _food_scan_notify(session, {"type": "kitchen.food.scan.state", "request_id": session.food_scan_request_id, "message": "准备接收图片…"})
                print(f"[FOOD-SCAN] upload start id={session.food_scan_request_id} source={session.food_scan_source} device={session.device_id}")
            elif kind == "kitchen.food.scan.stop":
                if not session.food_scan_receiving:
                    await _food_scan_notify(session, {"type": "kitchen.food.scan.error", "request_id": session.food_scan_request_id, "ok": False, "message": "没有收到图片"})
                    continue
                session.food_scan_receiving = False
                print(f"[FOOD-SCAN] upload complete id={session.food_scan_request_id} bytes={len(session.food_scan_image)}")
                await _food_scan_notify(session, {"type": "kitchen.food.scan.state", "request_id": session.food_scan_request_id, "message": "图片上传完成，正在识别…"})
                await _process_food_scan(session)
            elif kind == "kitchen.food.scan.cancel":
                session.food_scan_receiving = False
                session.food_scan_image.clear()
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
        if session.food_scan_receiving and not session.food_scan_processing:
            abandoned_id = session.food_scan_request_id
            session.food_scan_receiving = False
            session.food_scan_image.clear()
            print(f"[FOOD-SCAN-WARN] abandoned upload reset id={abandoned_id or 'unknown'}")
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

    session = ClientSession(
        ws=ws,
        device_id=HOMEAI_PRIMARY_DEVICE_ID,
        openclaw_user=OPENCLAW_USER,
    )
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

                incoming_trigger = str(msg.get("trigger") or "unknown")
                if incoming_trigger == "follow_up":
                    if not session.followup.is_active():
                        session.followup_candidate_authorized = False
                        session.recording = False
                        print(
                            f"[FOLLOWUP] candidate rejected outside window "
                            f"device={session.device_id}"
                        )
                        await send_state(ws, "idle")
                        continue
                    # Reserve the one-shot window immediately. The recent turn
                    # history remains available to the Context Judge, but a
                    # second concurrent no-wake candidate cannot enter.
                    session.followup.close_window("candidate_started")
                    session.followup_candidate_authorized = True
                else:
                    # An explicit wake/button turn is a deliberate fresh entry.
                    # It must never inherit a stale no-wake gate.
                    session.followup.reset_chain("explicit_ptt")
                    session.followup_candidate_authorized = False

                session.audio.clear()
                session.context = dict(msg.get("context") or {})
                session.diag_glass_mode = str(msg.get("diag_glass_mode") or "GLASS NORMAL")
                session.ptt_trigger = incoming_trigger
                session.recording = True
                await send_state(ws, "listening")
                current = (session.context.get("current") or {}).get("headline", "")
                print(
                    f"[PTT] start trigger={session.ptt_trigger} "
                    f"current={current}"
                )

            elif kind == "ptt.stop":
                was_recording = session.recording
                session.recording = False
                stop_trigger = str(msg.get("trigger") or session.ptt_trigger or "unknown")
                session.ptt_trigger = stop_trigger
                declared = ((msg.get("audio") or {}).get("bytes"))
                print(
                    f"[PTT] stop trigger={stop_trigger} "
                    f"received={len(session.audio)} declared={declared}"
                )
                if stop_trigger == "follow_up" and (
                    not was_recording or not session.followup_candidate_authorized
                ):
                    print("[FOLLOWUP] rejected/stale stop ignored")
                    session.audio.clear()
                    await send_state(ws, "idle")
                    continue
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
                if abort_trigger == "follow_up":
                    session.followup.reset_chain("followup_abort")
                    session.followup_candidate_authorized = False
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
        f"[CFG] followup timeout={FOLLOWUP_TIMEOUT_SEC:.1f}s "
        f"fence={FOLLOWUP_AUDIO_FENCE_SEC:.1f}s "
        f"judge_timeout={FOLLOWUP_JUDGE_TIMEOUT_SEC:.1f}s "
        f"cleanup_timeout={FOLLOWUP_JUDGE_CLEANUP_TIMEOUT_SEC:.1f}s "
        f"cleanup={FOLLOWUP_JUDGE_SESSION_CLEANUP}"
    )
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
    load_kitchen_prep_state()
    load_kitchen_shopping_state()
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
