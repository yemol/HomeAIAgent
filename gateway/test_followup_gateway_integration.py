#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
GW = (ROOT / "companion_gateway.py").read_text(encoding="utf-8")
FW = (ROOT.parent / "src" / "main.cpp").read_text(encoding="utf-8")


def require(text: str, needle: str) -> None:
    assert needle in text, f"missing required integration marker: {needle}"


def test_gateway_gate_order() -> None:
    process_start = GW.index("async def process_utterance")
    process_end = GW.index("\n\n\ndef _kitchen_clamp_timer_seconds", process_start)
    process = GW[process_start:process_end]

    judge_pos = process.index("await _judge_followup_context(session, transcript)")
    agent_pos = process.index("agent_started = time.perf_counter()")
    assert judge_pos < agent_pos, "Context Judge must run before local/OpenClaw execution"

    require(process, 'if decision != "continue":')
    require(process, 'session.followup.reset_chain("judge_ignore")')
    require(process, "followup_validated = not is_followup")
    require(process, 'session.followup.reset_chain("candidate_error")')
    require(process, '"type": "followup.arm"')
    require(process, "FOLLOWUP_AUDIO_FENCE_SEC")


def test_judge_isolated_from_primary_voice_session() -> None:
    judge_start = GW.index("async def _judge_followup_context")
    judge_end = GW.index("\n\nasync def openclaw_chat", judge_start)
    judge = GW[judge_start:judge_end]
    require(judge, '"x-openclaw-session-key": session_key')
    assert '"user": openclaw_user' not in judge
    require(GW, "homeai-followup-judge-")
    require(GW, '"sessions.delete"')
    require(GW, "FOLLOWUP_JUDGE_CLEANUP_TIMEOUT_SEC")
    require(judge, "attempts=1")


def test_ptt_gate_and_firmware_direct_input() -> None:
    require(GW, 'incoming_trigger == "follow_up"')
    require(GW, "session.followup.is_active()")
    require(GW, "followup_candidate_authorized")

    require(FW, 'else if (!strcmp(msgType, "followup.arm"))')
    require(FW, 'return autoFollowupCaptureActive ? "follow_up" : "wake_word";')
    require(FW, "startAutoCaptureFromFollowup")
    require(FW, "wakeLastFeedLevel >= currentAutoVadThreshold()")


if __name__ == "__main__":
    test_gateway_gate_order()
    test_judge_isolated_from_primary_voice_session()
    test_ptt_gate_and_firmware_direct_input()
    print("[PASS] follow-up Gateway/firmware integration guards")
