from typing import TYPE_CHECKING, Any

from ...diagnostics.events import CanonicalEventLogger
from ...schemas import WorkingState
from .pipeline import apply_skill_selection_to_state, resolve_skill_pipeline

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


def resolve_skill_hints(
    runner: "BrainRunner",
    *,
    intent: str,
    purpose: str,
    state: WorkingState,
    logger: CanonicalEventLogger,
) -> dict[str, Any]:
    normalized_intent = str(intent or "").strip()
    if not normalized_intent:
        return {}

    result = resolve_skill_pipeline(
        runner,
        intent=normalized_intent,
        purpose=purpose,
        state=state,
        logger=logger,
    )
    apply_skill_selection_to_state(state=state, result=result)

    hints: dict[str, Any] = {
        "context_budget_tier": result.context_budget,
        "skill_selection_mode": result.selection_mode,
        "skill_effective_count": result.effective_count,
        "skill_capacity": result.capacity,
    }
    if result.selected_refs:
        primary = result.primary_ref
        if primary is not None:
            hints.update(
                {
                    "skill_id": primary.skill_id,
                    "primary_skill_id": primary.skill_id,
                    "skill_version_hash": primary.version_hash,
                }
            )
        hints["resolved_skill_ids"] = [ref.skill_id for ref in result.selected_refs]
        hints["skill_refs"] = [
            {
                "skill_id": ref.skill_id,
                "version_hash": ref.version_hash,
            }
            for ref in result.selected_refs
        ]
    return hints


__all__ = ["resolve_skill_hints"]
