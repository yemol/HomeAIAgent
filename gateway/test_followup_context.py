#!/usr/bin/env python3
from __future__ import annotations

from context.followup import FollowUpState, build_followup_judge_prompt, parse_followup_decision


def test_state_window_and_history() -> None:
    state = FollowUpState(max_turns=2)
    assert not state.is_active(now=10.0)

    state.record_turn("黄金现在多少钱？", "现在是 900 元左右。")
    state.arm(10.0, now=100.0)
    assert state.is_active(now=105.0)
    assert 4.9 < state.remaining_sec(now=105.0) < 5.1
    assert not state.is_active(now=110.0)
    assert state.last_reason == "expired"

    state.record_turn("那昨天呢？", "昨天略低。")
    state.record_turn("差多少？", "大约相差若干。")
    payload = state.context_payload()
    assert len(payload) == 2
    assert payload[0]["user"] == "那昨天呢？"
    assert payload[1]["user"] == "差多少？"

    state.reset_chain("test")
    assert state.context_payload() == []
    assert not state.armed


def test_decision_parser_fail_closed() -> None:
    assert parse_followup_decision('{"decision":"continue","reason":"same topic"}')[0] == "continue"
    assert parse_followup_decision('```json\n{"decision":"ignore","reason":"new topic"}\n```')[0] == "ignore"
    assert parse_followup_decision('{"decision":"switch"}')[0] == "ignore"
    assert parse_followup_decision('not json')[0] == "ignore"
    assert parse_followup_decision('')[0] == "ignore"


def test_prompt_is_strict_context_gate() -> None:
    prompt = build_followup_judge_prompt(
        [{"user": "推荐一个晚餐", "assistant": "番茄牛肉盖饭。"}],
        "牛肉可以换鸡肉吗？",
    )
    assert "必须和当前上下文存在明确逻辑关系" in prompt
    assert "有歧义时选择 ignore" in prompt
    assert "牛肉可以换鸡肉吗" in prompt
    assert '"decision":"continue"|"ignore"' in prompt


if __name__ == "__main__":
    test_state_window_and_history()
    test_decision_parser_fail_closed()
    test_prompt_is_strict_context_gate()
    print("[PASS] follow-up context state/judge helpers")
