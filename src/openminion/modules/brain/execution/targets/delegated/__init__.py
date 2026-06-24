from types import SimpleNamespace
from typing import Any


def is_delegated_target(target: Any) -> bool:
    return str(getattr(target, "kind", "") or "").strip().lower() == "delegated"


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _goal_from_agent_command(*, decision: Any) -> str:
    seeded_commands = list(getattr(decision, "_seeded_commands", []) or [])
    command = seeded_commands[0] if seeded_commands else None
    if str(getattr(command, "kind", "") or "").strip().lower() != "agent":
        return ""
    params = getattr(command, "params", None)
    if isinstance(params, dict):
        for key in ("goal", "message", "text", "prompt", "task", "query"):
            text = _normalized_text(params.get(key))
            if text:
                return text
    return ""


def build_delegated_decision(*, decision: Any, goal: str) -> Any:
    target = getattr(decision, "execution_target", None)
    target_agent_id = str(getattr(target, "target_agent_id", "") or "").strip()
    delegated_goal = _goal_from_agent_command(decision=decision) or _normalized_text(
        goal
    )
    return SimpleNamespace(
        confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
        reason_code=str(getattr(decision, "reason_code", "") or "").strip()
        or "act_target_delegated",
        sub_intents=list(getattr(decision, "sub_intents", []) or []),
        rationale=str(getattr(decision, "rationale", "") or "").strip(),
        target_agent_id=target_agent_id,
        target_capability=str(getattr(target, "target_capability", "") or "").strip()
        or None,
        goal=delegated_goal,
        constraints="",
        synthesize_result=False,
        timeout_ms=None,
        question=None,
        answer=None,
    )


__all__ = [
    "build_delegated_decision",
    "is_delegated_target",
]
