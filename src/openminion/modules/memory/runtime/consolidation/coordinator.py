"""Typed contracts for memory consolidation runtime helpers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from openminion.modules.llm import RuntimeLLMHandle
from openminion.modules.memory.errors import InvalidArgumentError

MAINTENANCE_MODULE_STATE_KEY = "memory_context_maintenance"
_WORKING_STATE_MODULE_STATE_ATTR = "".join(("module", "_", "state"))


@dataclass(frozen=True)
class ConsolidationConfig:
    recent_rollout_limit: int = 256
    idle_seconds_before_eligible: int = 21600
    min_rate_limit_remaining_percent: int = 25
    consolidation_model: str | None = None

    def __post_init__(self) -> None:
        if self.recent_rollout_limit <= 0:
            raise InvalidArgumentError(
                "recent_rollout_limit must be a positive integer"
            )
        if self.idle_seconds_before_eligible < 0:
            raise InvalidArgumentError(
                "idle_seconds_before_eligible must be non-negative"
            )
        if not 0 <= self.min_rate_limit_remaining_percent <= 100:
            raise InvalidArgumentError(
                "min_rate_limit_remaining_percent must be within [0, 100]"
            )


@dataclass(frozen=True)
class ExtractionPayload:
    session_id: str
    agent_id: str
    candidate_refs: list[dict[str, Any]] = field(default_factory=list)
    topic_clusters: list[dict[str, Any]] = field(default_factory=list)
    contradiction_hints: list[dict[str, Any]] = field(default_factory=list)
    duplicate_hints: list[dict[str, Any]] = field(default_factory=list)
    evidence_window: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MergeDecision:
    candidate_id: str
    action: str
    reasoning: str = ""
    target_scope: str = ""


@dataclass(frozen=True)
class MergeDecisions:
    decisions: list[MergeDecision] = field(default_factory=list)
    model_name: str = ""
    review_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarkerWriteResult:
    applied: bool
    reason_code: str
    audit_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsolidationCycleResult:
    applied: bool
    reason_code: str
    eligibility_reason_code: str
    state_hash: str = ""
    payload: ExtractionPayload | None = None
    merge_decisions: MergeDecisions | None = None
    write_result: dict[str, Any] = field(default_factory=dict)
    marker_result: MarkerWriteResult | None = None


def apply_consolidation_marker(
    working_state: Any,
    *,
    session_id: str,
    turn_id: str,
    marker: str,
    state_hash: str,
    input_ref: str = "",
    output_ref: str = "",
    reason: str = "",
) -> MarkerWriteResult:
    module_state = getattr(working_state, _WORKING_STATE_MODULE_STATE_ATTR, None)
    if not isinstance(module_state, dict):
        module_state = {}
        setattr(working_state, _WORKING_STATE_MODULE_STATE_ATTR, module_state)
    maintenance = module_state.get(MAINTENANCE_MODULE_STATE_KEY)
    if not isinstance(maintenance, dict):
        maintenance = {}
        module_state[MAINTENANCE_MODULE_STATE_KEY] = maintenance

    prior_hash = str(maintenance.get("last_consolidation_state_hash", "") or "").strip()
    audit_payload = {
        "operation": "memory_consolidation",
        "session_id": str(session_id or "").strip(),
        "turn_id": str(turn_id or "").strip(),
        "marker": str(marker or "").strip(),
        "state_hash": str(state_hash or "").strip(),
        "input_ref": str(input_ref or "").strip(),
        "output_ref": str(output_ref or "").strip(),
        "reason": str(reason or "").strip(),
    }
    if prior_hash and prior_hash == state_hash:
        return MarkerWriteResult(
            applied=False,
            reason_code="ALREADY_CONSOLIDATED",
            audit_payload=audit_payload,
        )

    maintenance["last_consolidation_marker"] = str(marker or "").strip()
    maintenance["last_consolidation_state_hash"] = str(state_hash or "").strip()
    return MarkerWriteResult(
        applied=True,
        reason_code="OK",
        audit_payload=audit_payload,
    )


class ConsolidationCoordinator(Protocol):
    def run_extraction(
        self,
        session_id: str,
        agent_id: str,
        recent_rollout_limit: int,
    ) -> ExtractionPayload: ...

    def run_merge(
        self,
        payload: ExtractionPayload,
        consolidation_model_handle: RuntimeLLMHandle,
    ) -> MergeDecisions: ...


def run_consolidation_cycle(
    memory_service: Any,
    *,
    working_state: Any,
    primary_model_handle: RuntimeLLMHandle,
    config: ConsolidationConfig,
    target_scope: str,
    turn_id: str,
    now: datetime | None = None,
    recent_rollout_probe: Any | None = None,
    rate_limit_remaining_percent_probe: Any | None = None,
) -> ConsolidationCycleResult:
    from openminion.modules.memory.runtime.consolidation.eligibility import (
        ConsolidationEligibilityChecker,
    )
    from openminion.modules.memory.runtime.consolidation.extract import (
        extract_consolidation_payload,
    )
    from openminion.modules.memory.runtime.consolidation.merge import (
        apply_merge_decisions_via_service,
        resolve_consolidation_model_handle,
        run_consolidation_merge,
    )

    target_now = now or datetime.now(timezone.utc)
    session_id = str(getattr(working_state, "session_id", "") or "").strip()
    agent_id = str(getattr(working_state, "agent_id", "") or "").strip()
    checker = ConsolidationEligibilityChecker(
        memory_service,
        recent_rollout_probe=recent_rollout_probe,
        rate_limit_remaining_percent_probe=rate_limit_remaining_percent_probe,
        working_state_probe=lambda _session_id, _agent_id: working_state,
    )
    eligibility = checker.is_eligible(
        session_id=session_id,
        agent_id=agent_id,
        config=config,
        now=target_now,
    )
    if not eligibility.is_eligible:
        return ConsolidationCycleResult(
            applied=False,
            reason_code=eligibility.reason_code,
            eligibility_reason_code=eligibility.reason_code,
            state_hash=eligibility.state_hash,
        )

    payload = extract_consolidation_payload(
        memory_service,
        session_id=session_id,
        agent_id=agent_id,
        recent_rollout_limit=config.recent_rollout_limit,
        now=target_now,
    )
    consolidation_handle = resolve_consolidation_model_handle(
        primary_model_handle,
        config,
    )
    merge_decisions = run_consolidation_merge(payload, consolidation_handle)
    write_result = apply_merge_decisions_via_service(
        memory_service,
        payload=payload,
        merge_decisions=merge_decisions,
        target_scope=target_scope,
    )
    marker_result = apply_consolidation_marker(
        working_state,
        session_id=session_id,
        turn_id=turn_id,
        marker=target_now.isoformat(),
        state_hash=eligibility.state_hash,
        input_ref=f"payload:{len(payload.candidate_refs)}",
        output_ref=f"merge:{len(merge_decisions.decisions)}",
        reason="consolidation_cycle",
    )
    return ConsolidationCycleResult(
        applied=bool(marker_result.applied),
        reason_code=marker_result.reason_code,
        eligibility_reason_code=eligibility.reason_code,
        state_hash=eligibility.state_hash,
        payload=payload,
        merge_decisions=merge_decisions,
        write_result=write_result,
        marker_result=marker_result,
    )


__all__ = [
    "MAINTENANCE_MODULE_STATE_KEY",
    "MarkerWriteResult",
    "ConsolidationCycleResult",
    "ConsolidationConfig",
    "ConsolidationCoordinator",
    "ExtractionPayload",
    "MergeDecision",
    "MergeDecisions",
    "apply_consolidation_marker",
    "run_consolidation_cycle",
]
