from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

CostUnit = Literal[
    "llm_call",
    "tool_call",
    "a2a_call",
    "retry_attempt",
    "token",
    "time_ms",
]
BudgetEnforcementDecision = Literal[
    "allow",
    "warn",
    "hard_stop",
]
ScopeKind = Literal[
    "agent",
    "session",
    "mission",
    "turn",
    "run",
]
COST_UNIT_VALUES: tuple[CostUnit, ...] = (
    "llm_call",
    "tool_call",
    "a2a_call",
    "retry_attempt",
    "token",
    "time_ms",
)

BUDGET_ENFORCEMENT_DECISION_VALUES: tuple[BudgetEnforcementDecision, ...] = (
    "allow",
    "warn",
    "hard_stop",
)

SCOPE_KIND_VALUES: tuple[ScopeKind, ...] = (
    "agent",
    "session",
    "mission",
    "turn",
    "run",
)


class CostAttribution(BaseModel):
    """One charged action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    cost_unit: CostUnit
    amount: float
    charged_agent_id: str
    charged_session_id: str
    charged_mission_id: str | None = None
    parent_action_id: str | None = None
    source_owner: str


class BudgetEnforcementDecisionEvent(BaseModel):
    """Audit event for one enforcement decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: BudgetEnforcementDecision
    budget_name: str
    reason_code: str
    scope_kind: ScopeKind
    attribution_ref: str
    recorded_at: str


class QuotaEnvelope(BaseModel):
    """Durable per-scope quota snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: ScopeKind
    scope_id: str
    cost_unit: CostUnit
    remaining: float
    limit: float
    window_started_at: str
    window_ends_at: str


class CostLedger(Protocol):
    """Typed ledger collaborator."""

    def debit(self, attribution: CostAttribution) -> None: ...


class QuotaEnvelopeStore(Protocol):
    """Typed store for quota lookup."""

    def get(
        self,
        scope_kind: ScopeKind,
        scope_id: str,
        cost_unit: CostUnit,
    ) -> QuotaEnvelope: ...


def project_action_to_cost_attribution(
    action: Any,
    *,
    charged_agent_id: str,
    charged_session_id: str,
    charged_mission_id: str | None = None,
    parent_action_id: str | None = None,
    source_owner: str,
) -> CostAttribution:
    """Project a structural action into ``CostAttribution``."""

    try:
        action_id = action.action_id
        cost_unit = action.cost_unit
        amount = action.amount
    except AttributeError as exc:
        raise TypeError(
            "project_action_to_cost_attribution requires action_id, "
            "cost_unit, and amount attributes; got "
            f"{type(action).__name__}"
        ) from exc

    return CostAttribution(
        action_id=action_id,
        cost_unit=cost_unit,
        amount=amount,
        charged_agent_id=charged_agent_id,
        charged_session_id=charged_session_id,
        charged_mission_id=charged_mission_id,
        parent_action_id=parent_action_id,
        source_owner=source_owner,
    )


def apply_cost_attribution(
    attribution: CostAttribution,
    *,
    ledger: CostLedger,
) -> CostAttribution:
    """Debit the ledger and return the recorded attribution."""

    ledger.debit(attribution)
    return attribution


def load_quota_envelope(
    scope_kind: ScopeKind,
    scope_id: str,
    cost_unit: CostUnit,
    *,
    store: QuotaEnvelopeStore,
) -> QuotaEnvelope:
    """Return the current quota envelope for the scope."""

    return store.get(scope_kind, scope_id, cost_unit)


REASON_CODE_QUOTA_EXHAUSTED = "quota_exhausted"
REASON_CODE_QUOTA_WARNING = "quota_warning"
REASON_CODE_WITHIN_BUDGET = "within_budget"


def evaluate_budget_enforcement(
    attribution: CostAttribution,
    *,
    envelope: QuotaEnvelope,
    warn_ratio: float = 0.9,
    recorded_at: str,
    budget_name: str,
) -> BudgetEnforcementDecisionEvent:
    """Evaluate one attribution against one quota envelope."""

    if attribution.cost_unit != envelope.cost_unit:
        raise ValueError(
            "evaluate_budget_enforcement requires matching cost_unit on "
            f"attribution ({attribution.cost_unit}) and envelope "
            f"({envelope.cost_unit})"
        )

    decision: BudgetEnforcementDecision
    reason_code: str

    if envelope.remaining < attribution.amount:
        decision = "hard_stop"
        reason_code = REASON_CODE_QUOTA_EXHAUSTED
    else:
        projected_remaining = envelope.remaining - attribution.amount
        warn_floor = envelope.limit * (1.0 - warn_ratio)
        if projected_remaining < warn_floor:
            decision = "warn"
            reason_code = REASON_CODE_QUOTA_WARNING
        else:
            decision = "allow"
            reason_code = REASON_CODE_WITHIN_BUDGET

    return BudgetEnforcementDecisionEvent(
        decision=decision,
        budget_name=budget_name,
        reason_code=reason_code,
        scope_kind=envelope.scope_kind,
        attribution_ref=attribution.action_id,
        recorded_at=recorded_at,
    )


__all__ = (
    "BUDGET_ENFORCEMENT_DECISION_VALUES",
    "BudgetEnforcementDecision",
    "BudgetEnforcementDecisionEvent",
    "COST_UNIT_VALUES",
    "CostAttribution",
    "CostLedger",
    "CostUnit",
    "QuotaEnvelope",
    "QuotaEnvelopeStore",
    "REASON_CODE_QUOTA_EXHAUSTED",
    "REASON_CODE_QUOTA_WARNING",
    "REASON_CODE_WITHIN_BUDGET",
    "SCOPE_KIND_VALUES",
    "ScopeKind",
    "apply_cost_attribution",
    "evaluate_budget_enforcement",
    "load_quota_envelope",
    "project_action_to_cost_attribution",
)
