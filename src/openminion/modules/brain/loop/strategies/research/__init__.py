from types import SimpleNamespace
from typing import Any

from .handler import RESEARCH_MODE, ResearchMode
from .schemas import ConvergenceCheck, ResearchFinding, ResearchPayload

RESEARCH_PROFILE_NAME = "research"


def build_research_decision(*, decision: Any, query: str) -> Any:
    internal = SimpleNamespace(
        confidence=float(getattr(decision, "confidence", 1.0) or 1.0),
        reason_code=str(getattr(decision, "reason_code", "") or "").strip()
        or "act_profile_research",
        research_query=str(query or "").strip(),
        research_scope="",
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
    "ConvergenceCheck",
    "RESEARCH_MODE",
    "RESEARCH_PROFILE_NAME",
    "ResearchFinding",
    "ResearchMode",
    "ResearchPayload",
    "build_research_decision",
]
