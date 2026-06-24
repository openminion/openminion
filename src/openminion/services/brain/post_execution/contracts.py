from dataclasses import dataclass
from typing import Any

from openminion.modules.brain.schemas import WorkingState


@dataclass(frozen=True)
class _MissionResetPreview:
    parsed_state: WorkingState | None
    mission_runtime_active: bool
    route_action: str
    route_objective: str
    route_fork_input: str

    @property
    def mission_active(self) -> bool:
        """Backward-compatible alias for pre-rename reset call sites."""
        return self.mission_runtime_active


@dataclass(frozen=True)
class _TurnResetPreservation:
    previous_goal: str
    normalized_current_input: str
    pending_confirmation_command: Any
    decision_feasibility_state: dict[str, Any]
    preserve_existing_plan: bool
    preserve_followup_goal: bool
    preserve_decision_state: bool
    preserve_continuation_guard: bool
    preserve_continuation_reply: bool
    preserve_pending_confirmation: bool
    continuation_constraints: list[str]
    # BBPC: the policy-confirmation parser's verdict on the new input,
    parsed_confirmation_reply: str = ""


__all__ = [
    "_MissionResetPreview",
    "_TurnResetPreservation",
]
