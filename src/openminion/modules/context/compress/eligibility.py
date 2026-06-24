"""Service-owned eligibility contract for explicit self-compaction."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from openminion.modules.context.constants import (
    COMPACTION_REASON_ALREADY_COMPACTED_THIS_TURN as REASON_ALREADY_COMPACTED_THIS_TURN,
    COMPACTION_REASON_BELOW_THRESHOLD as REASON_BELOW_THRESHOLD,
    COMPACTION_REASON_CONSOLIDATION_NOT_YET_RUN as REASON_CONSOLIDATION_NOT_YET_RUN,
    COMPACTION_REASON_OK as REASON_OK,
)
from openminion.modules.context.schemas import _stable_hash
from openminion.modules.memory import MAINTENANCE_MODULE_STATE_KEY

_WORKING_STATE_MODULE_STATE_ATTR = "".join(("module", "_", "state"))


@dataclass(frozen=True)
class CompactionBudgetState:
    max_prompt_tokens: int
    compaction_trigger_percent: float = 0.85
    consolidation_eligible: bool = False
    consolidation_completed: bool = False
    idle_hint: bool = False
    task_boundary_hint: bool = False


@dataclass(frozen=True)
class EligibilityResult:
    is_eligible: bool
    reason_code: str
    state_hash: str
    token_pressure_ratio: float


class CompactionEligibility(Protocol):
    def is_eligible(
        self,
        working_state: Any,
        *,
        prompt_token_estimate: int,
        budget_state: CompactionBudgetState,
        now: datetime,
    ) -> EligibilityResult: ...


def compaction_state_hash(
    working_state: Any,
    *,
    prompt_token_estimate: int,
    budget_state: CompactionBudgetState,
) -> str:
    return _stable_hash(
        {
            "session_id": str(getattr(working_state, "session_id", "") or ""),
            "agent_id": str(getattr(working_state, "agent_id", "") or ""),
            "goal": str(getattr(working_state, "goal", "") or ""),
            "pending_turn_context": getattr(
                working_state, "pending_turn_context", None
            ),
            "prompt_token_estimate": max(0, int(prompt_token_estimate or 0)),
            "max_prompt_tokens": max(1, int(budget_state.max_prompt_tokens or 1)),
            "idle_hint": bool(budget_state.idle_hint),
            "task_boundary_hint": bool(budget_state.task_boundary_hint),
        }
    )


class DefaultCompactionEligibility:
    def __init__(self, *, compaction_trigger_percent: float = 0.85) -> None:
        self._default_threshold = float(compaction_trigger_percent)

    def is_eligible(
        self,
        working_state: Any,
        *,
        prompt_token_estimate: int,
        budget_state: CompactionBudgetState,
        now: datetime,
    ) -> EligibilityResult:
        del now
        max_tokens = max(1, int(budget_state.max_prompt_tokens or 1))
        ratio = max(0.0, float(prompt_token_estimate or 0) / float(max_tokens))
        state_hash = compaction_state_hash(
            working_state,
            prompt_token_estimate=prompt_token_estimate,
            budget_state=budget_state,
        )
        if (
            budget_state.consolidation_eligible
            and not budget_state.consolidation_completed
        ):
            return EligibilityResult(
                is_eligible=False,
                reason_code=REASON_CONSOLIDATION_NOT_YET_RUN,
                state_hash=state_hash,
                token_pressure_ratio=ratio,
            )
        module_state = getattr(working_state, _WORKING_STATE_MODULE_STATE_ATTR, None)
        maintenance = (
            module_state.get(MAINTENANCE_MODULE_STATE_KEY, {})
            if isinstance(module_state, dict)
            else {}
        )
        prior_hash = (
            str(maintenance.get("last_compaction_state_hash", "") or "").strip()
            if isinstance(maintenance, dict)
            else ""
        )
        if prior_hash and prior_hash == state_hash:
            return EligibilityResult(
                is_eligible=False,
                reason_code=REASON_ALREADY_COMPACTED_THIS_TURN,
                state_hash=state_hash,
                token_pressure_ratio=ratio,
            )
        threshold = float(
            budget_state.compaction_trigger_percent or self._default_threshold
        )
        if ratio < threshold:
            return EligibilityResult(
                is_eligible=False,
                reason_code=REASON_BELOW_THRESHOLD,
                state_hash=state_hash,
                token_pressure_ratio=ratio,
            )
        return EligibilityResult(
            is_eligible=True,
            reason_code=REASON_OK,
            state_hash=state_hash,
            token_pressure_ratio=ratio,
        )


__all__ = [
    "CompactionBudgetState",
    "CompactionEligibility",
    "DefaultCompactionEligibility",
    "EligibilityResult",
    "REASON_ALREADY_COMPACTED_THIS_TURN",
    "REASON_BELOW_THRESHOLD",
    "REASON_CONSOLIDATION_NOT_YET_RUN",
    "REASON_OK",
    "compaction_state_hash",
]
