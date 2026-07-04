"""Compact context card for active goal runs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.schemas.goals import Goal

from .ledger import GoalRunLedgerSummary
from .loop import GoalRunState


class GoalContextCard(BaseModel):
    """Bounded recent-context card for one active goal run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_id: str = Field(min_length=1)
    status: str
    success_criteria: tuple[str, ...]
    deliverables: tuple[str, ...]
    next_instruction: str = ""
    blockers: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    cap_summary: str


def build_goal_context_card(
    *,
    goal: Goal,
    state: GoalRunState,
    summary: GoalRunLedgerSummary | None = None,
) -> GoalContextCard:
    ledger_summary = summary or GoalRunLedgerSummary(run_id=state.run_id)
    return GoalContextCard(
        goal_id=goal.goal_id,
        status=state.status.value,
        success_criteria=tuple(item.description for item in goal.success_criteria),
        deliverables=tuple(item.description for item in goal.deliverables),
        next_instruction=state.latest_next_instruction
        or ledger_summary.latest_next_instruction,
        blockers=tuple(item.descriptor for item in goal.external_blockers),
        evidence_refs=ledger_summary.evidence_refs or state.latest_evidence_refs,
        cap_summary=(
            f"turns={state.turn_count}/{state.caps.max_auto_turns}; "
            f"wall_clock={state.caps.max_wall_clock_seconds}s; "
            f"no_progress={state.repeated_no_progress_count}/"
            f"{state.caps.repeated_no_progress_limit}"
        ),
    )


def render_goal_context_card(card: GoalContextCard) -> str:
    lines = [
        f"Active goal: {card.goal_id}",
        f"Status: {card.status}",
        "Definition of done:",
    ]
    lines.extend(f"- {item}" for item in card.success_criteria[:5])
    if card.deliverables:
        lines.append("Deliverables:")
        lines.extend(f"- {item}" for item in card.deliverables[:5])
    if card.next_instruction:
        lines.extend(["Current next step:", f"- {card.next_instruction}"])
    if card.blockers:
        lines.append("Known blockers:")
        lines.extend(f"- {item}" for item in card.blockers[:5])
    if card.evidence_refs:
        lines.append("Evidence refs:")
        lines.extend(f"- {item}" for item in card.evidence_refs[:5])
    lines.append("Caps: " + card.cap_summary)
    return "\n".join(lines)


__all__ = [
    "GoalContextCard",
    "build_goal_context_card",
    "render_goal_context_card",
]
