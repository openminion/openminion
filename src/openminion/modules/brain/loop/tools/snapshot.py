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
            tool_results=[
                item
                for item in list(data.get("tool_results", []) or [])
                if isinstance(item, dict)
            ],
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
    result = messages[:2]
    result.append(
        {"role": "system", "content": f"[{len(messages) - 4} messages compressed]"}
    )
    for m in reversed(messages[2:]):
        candidate = result + [m]
        if sum(len(json.dumps(c, default=str)) for c in candidate) <= max_chars:
            result.append(m)
        else:
            break
    return result


def hash_args(args: dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
