from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LoopToolCallRecord:
    tool_name: str
    args_hash: str
    result_summary: str


@dataclass(slots=True)
class LoopSnapshot:
    turn_scope_id: str
    iteration_index: int
    message_transcript: list[dict[str, Any]]
    tool_call_history: list[LoopToolCallRecord]
    budgets_consumed: dict[str, Any]
    profile_name: str
    model: str
    allowed_tools: frozenset[str]
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_scope_id": self.turn_scope_id,
            "iteration_index": self.iteration_index,
            "message_transcript": self.message_transcript,
            "tool_call_history": [
                {
                    "tool_name": r.tool_name,
                    "args_hash": r.args_hash,
                    "result_summary": r.result_summary,
                }
                for r in self.tool_call_history
            ],
            "budgets_consumed": self.budgets_consumed,
            "profile_name": self.profile_name,
            "model": self.model,
            "allowed_tools": sorted(self.allowed_tools),
            "tool_results": list(self.tool_results),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopSnapshot:
        return cls(
            turn_scope_id=data["turn_scope_id"],
            iteration_index=data["iteration_index"],
            message_transcript=data["message_transcript"],
            tool_call_history=[
                LoopToolCallRecord(**r) for r in data.get("tool_call_history", [])
            ],
            budgets_consumed=data.get("budgets_consumed", {}),
            profile_name=data["profile_name"],
            model=data["model"],
            allowed_tools=frozenset(data.get("allowed_tools", [])),
            tool_results=list(data.get("tool_results", [])),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> LoopSnapshot:
        return cls.from_dict(json.loads(raw))


def compress_transcript(
    messages: list[dict[str, Any]], max_chars: int = 10000
) -> list[dict[str, Any]]:
    total = sum(len(json.dumps(m, default=str)) for m in messages)
    if total <= max_chars:
        return messages
    prefix = messages[:2]
    marker = {
        "role": "system",
        "content": f"[{len(messages) - 4} messages compressed]",
    }
    retained: list[dict[str, Any]] = []
    for message in reversed(messages[2:]):
        candidate = [*prefix, marker, message, *reversed(retained)]
        if sum(len(json.dumps(item, default=str)) for item in candidate) > max_chars:
            break
        retained.append(message)
    marker["content"] = (
        f"[{len(messages) - len(prefix) - len(retained)} messages compressed]"
    )
    return [
        *prefix,
        marker,
        *reversed(retained),
    ]


def hash_args(args: dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
