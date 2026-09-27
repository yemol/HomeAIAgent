from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FollowUpTurn:
    user: str
    assistant: str


@dataclass
class FollowUpState:
    """Ephemeral in-memory continuation state for one companion connection.

    This object never creates or owns an OpenClaw conversation. It only gates
    whether a no-wake utterance is allowed to enter the already-existing voice
    conversation.
    """

    turns: list[FollowUpTurn] = field(default_factory=list)
    armed: bool = False
    expires_at: float = 0.0
    arm_started_at: float = 0.0
    last_reason: str = "boot"
    max_turns: int = 4

    def reset_chain(self, reason: str = "reset") -> None:
        self.turns.clear()
        self.armed = False
        self.expires_at = 0.0
        self.arm_started_at = 0.0
        self.last_reason = reason

    def close_window(self, reason: str = "closed") -> None:
        self.armed = False
        self.expires_at = 0.0
        self.arm_started_at = 0.0
        self.last_reason = reason

    def record_turn(self, user: str, assistant: str) -> None:
        user_text = str(user or "").strip()
        assistant_text = str(assistant or "").strip()
        if not user_text or not assistant_text:
            return
        self.turns.append(FollowUpTurn(user=user_text, assistant=assistant_text))
        if len(self.turns) > max(1, int(self.max_turns)):
            del self.turns[: len(self.turns) - int(self.max_turns)]

    def arm(self, timeout_sec: float, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        timeout = max(0.0, float(timeout_sec))
        self.armed = bool(self.turns) and timeout > 0.0
        self.arm_started_at = current if self.armed else 0.0
        self.expires_at = current + timeout if self.armed else 0.0
        self.last_reason = "armed" if self.armed else "arm_skipped"

    def is_active(self, *, now: float | None = None) -> bool:
        if not self.armed:
            return False
        current = time.monotonic() if now is None else float(now)
        if current >= self.expires_at:
            self.close_window("expired")
            return False
        return True

    def remaining_sec(self, *, now: float | None = None) -> float:
        if not self.is_active(now=now):
            return 0.0
        current = time.monotonic() if now is None else float(now)
        return max(0.0, self.expires_at - current)

    def context_payload(self) -> list[dict[str, str]]:
        return [
            {"user": turn.user, "assistant": turn.assistant}
            for turn in self.turns
        ]


def build_followup_judge_prompt(
    turns: list[dict[str, str]],
    candidate: str,
) -> str:
    compact_turns = []
    for item in turns[-4:]:
        user = str(item.get("user") or "").strip()
        assistant = str(item.get("assistant") or "").strip()
        if user and assistant:
            compact_turns.append({"user": user, "assistant": assistant})

    context_json = json.dumps(compact_turns, ensure_ascii=False, separators=(",", ":"))
    candidate_json = json.dumps(str(candidate or "").strip(), ensure_ascii=False)
    return f"""你是 HomeAIAgent A5.0 Follow-up Context Judge。\n\n任务：判断“候选语音”是否是当前会话上下文的自然延续。\n\n硬规则：\n1. 只允许语义上依赖、追问、补充、修正、指代或继续执行当前上下文的内容。\n2. 任何新主题、旁人说话、电视/环境语音、无法确定指向、纯闲聊插入，全部 ignore。\n3. 不要因为候选语音本身是一个合理问题就 continue。它必须和当前上下文存在明确逻辑关系。\n4. 有歧义时选择 ignore。\n5. 不回答候选语音本身，不调用工具，不扩展任务。\n\n当前会话最近轮次：\n{context_json}\n\n候选语音：\n{candidate_json}\n\n只输出一行 JSON，不要 Markdown：\n{{\"decision\":\"continue\"|\"ignore\",\"reason\":\"简短原因\"}}\n"""


def parse_followup_decision(raw: Any) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "ignore", "empty_judge_output"

    # Accept strict JSON, fenced JSON, or a response containing one object.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except Exception:
        return "ignore", "invalid_judge_json"

    if not isinstance(payload, dict):
        return "ignore", "invalid_judge_shape"

    decision = str(payload.get("decision") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()[:160]
    if decision != "continue":
        return "ignore", reason or "judge_rejected"
    return "continue", reason or "context_continuation"
