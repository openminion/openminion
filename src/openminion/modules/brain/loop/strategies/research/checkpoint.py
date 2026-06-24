from typing import Any

from .findings import normalized_text
from .schemas import ResearchFinding


def build_checkpoint_state(
    *,
    query: str,
    next_iteration: int,
    findings: list[dict[str, Any]],
    resume_count: int,
) -> dict[str, Any]:
    return {
        "query": query,
        "next_iteration": next_iteration,
        "findings": list(findings),
        "resume_count": int(resume_count),
    }


def normalize_checkpoint_state(state: dict[str, Any]) -> dict[str, Any]:
    """Normalize older in-tree checkpoint payloads to the canonical findings shape."""
    if "next_phase_index" not in state:
        return state
    objective = normalized_text(state.get("objective") or state.get("query"))
    phase_outputs: dict[str, str] = dict(state.get("phase_outputs") or {})
    findings: list[dict[str, Any]] = []
    for idx, (phase, content) in enumerate(phase_outputs.items()):
        findings.append(
            ResearchFinding(
                iteration=idx,
                source_tool="plan",
                source_query=phase,
                content=normalized_text(content),
            ).model_dump(mode="python")
        )
    return build_checkpoint_state(
        query=objective,
        next_iteration=int(state.get("next_phase_index", 0) or 0),
        findings=findings,
        resume_count=int(state.get("resume_count", 0) or 0),
    )
