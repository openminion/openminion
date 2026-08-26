from types import SimpleNamespace
from typing import Any


def is_delegated_target(target: Any) -> bool:
    return str(getattr(target, "kind", "") or "").strip().lower() == "delegated"


def build_delegated_decision(*, decision: Any, goal: str) -> Any:
    target = getattr(decision, "execution_target", None)
    target_agent_id = str(getattr(target, "target_agent_id", "") or "").strip()
    return SimpleNamespace(
        confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
        reason_code=str(getattr(decision, "reason_code", "") or "").strip()
        or "act_target_delegated",
        sub_intents=list(getattr(decision, "sub_intents", []) or []),
        rationale=str(getattr(decision, "rationale", "") or "").strip(),
        target_agent_id=target_agent_id,
        goal=str(goal or "").strip(),
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
