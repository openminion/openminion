from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.constants import BRAIN_ACT_PROFILE_CODING
from .contracts import CODING_ALLOWED_TOOLS, CODING_V1_ALLOWED_TOOLS
from .handler import (
    CodingMode,
    CodingProfileRunner,
    execute_coding_profile,
    prepare_coding_profile,
)

CODING_PROFILE_NAME = "coding"


def build_coding_decision(*, decision: Any, goal: str) -> Any:
    internal = SimpleNamespace(
        act_profile=BRAIN_ACT_PROFILE_CODING,
        confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
        reason_code=str(getattr(decision, "reason_code", "") or "").strip()
        or "act_profile_coding",
        objective=str(goal or "").strip(),
        sub_intents=list(getattr(decision, "sub_intents", []) or []),
        rationale=str(getattr(decision, "rationale", "") or "").strip(),
        question=None,
        answer=None,
    )
    seeded_commands = list(getattr(decision, "_seeded_commands", []) or [])
    if seeded_commands:
        internal._seeded_commands = seeded_commands
    entry_response = getattr(decision, "_entry_response", None)
    if entry_response is not None:
        internal._entry_response = entry_response
    return internal


__all__ = [
    "CODING_ALLOWED_TOOLS",
    "CODING_PROFILE_NAME",
    "CodingProfileRunner",
    "CODING_V1_ALLOWED_TOOLS",
    "CodingMode",
    "build_coding_decision",
    "execute_coding_profile",
    "prepare_coding_profile",
]
