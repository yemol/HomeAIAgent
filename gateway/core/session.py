from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from context.followup import FollowUpState


@dataclass
class ClientSession:
    ws: Any

    # Device identity/routing.
    # - companion: owns an OpenClaw conversation
    # - speaker: owns no LLM session; it is an audio sink bound to parent_device_id
    device_id: str = "homeai-agent-main-01"
    device_role: str = "companion"
    parent_device_id: str = ""
    openclaw_user: str = "home-ai-agent:main"
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
    playback_completed_segments: int = 0
    playback_total_segments: int = 0

    # A5.0 no-wake continuation gate. This remains process-local and is never
    # persisted. The candidate flag is set only when a follow_up PTT start was
    # accepted inside an armed window.
    followup: FollowUpState = field(default_factory=FollowUpState)
    followup_candidate_authorized: bool = False

    # NetworkSpeaker control ACKs.
    speaker_volume_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    # Display policy handshake.
    display_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    display_expected_command_id: str = ""
    display_last_ack: dict[str, Any] = field(default_factory=dict)
    display_last_confirmed_sleeping: bool | None = None
    display_policy_task: Any = None

    # Explicit user voice control for Glass2 only.
    glass2_ack_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    glass2_expected_command_id: str = ""
