from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Optional, get_args

import pytest

from openminion.modules.runtime import cost as ce


@dataclass
class _Action:
    action_id: str
    cost_unit: ce.CostUnit
    amount: float


class _Ledger:
    def __init__(self) -> None:
        self.debits: list[ce.CostAttribution] = []

    def debit(self, attribution: ce.CostAttribution) -> None:
        self.debits.append(attribution)


class _DurableStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], ce.QuotaEnvelope] = {}

    def put(self, envelope: ce.QuotaEnvelope) -> None:
        key = (envelope.scope_kind, envelope.scope_id, envelope.cost_unit)
        self._records[key] = envelope

    def get(
        self,
        scope_kind: ce.ScopeKind,
        scope_id: str,
        cost_unit: ce.CostUnit,
    ) -> ce.QuotaEnvelope:
        return self._records[(scope_kind, scope_id, cost_unit)]


_FORBIDDEN_PROSE_FIELDS = {"content", "prose", "reasoning", "rationale", "verdict"}


def test_cost_unit_literal_is_closed_set() -> None:
    assert set(get_args(ce.CostUnit)) == {
        "llm_call",
        "tool_call",
        "a2a_call",
        "retry_attempt",
        "token",
        "time_ms",
    }
    assert ce.COST_UNIT_VALUES == tuple(get_args(ce.CostUnit))


def test_budget_enforcement_decision_literal_is_closed_set() -> None:
    assert set(get_args(ce.BudgetEnforcementDecision)) == {
        "allow",
        "warn",
        "hard_stop",
    }
    assert ce.BUDGET_ENFORCEMENT_DECISION_VALUES == tuple(
        get_args(ce.BudgetEnforcementDecision)
    )


def test_scope_kind_literal_is_closed_set() -> None:
    assert set(get_args(ce.ScopeKind)) == {
        "agent",
        "session",
        "mission",
        "turn",
        "run",
    }
    assert ce.SCOPE_KIND_VALUES == tuple(get_args(ce.ScopeKind))


def test_invalid_cost_unit_rejected_by_schema() -> None:
    with pytest.raises(Exception):  # pydantic.ValidationError
        ce.CostAttribution(
            action_id="a-1",
            cost_unit="invalid_unit",  # type: ignore[arg-type]
            amount=1.0,
            charged_agent_id="agent-A",
            charged_session_id="session-S",
            source_owner="test",
        )


def test_invalid_scope_kind_rejected_by_envelope_schema() -> None:
    with pytest.raises(Exception):
        ce.QuotaEnvelope(
            scope_kind="cluster",  # type: ignore[arg-type]
            scope_id="x",
            cost_unit="tool_call",
            remaining=1.0,
            limit=10.0,
            window_started_at="t0",
            window_ends_at="t1",
        )


def test_invalid_decision_rejected_by_event_schema() -> None:
    with pytest.raises(Exception):
        ce.BudgetEnforcementDecisionEvent(
            decision="soft_stop",  # type: ignore[arg-type]
            budget_name="b",
            reason_code="r",
            scope_kind="session",
            attribution_ref="a",
            recorded_at="t",
        )


def test_module_level_value_tuples_match_literals_at_load_time() -> None:
    importlib.reload(ce)
    assert ce.COST_UNIT_VALUES == tuple(get_args(ce.CostUnit))
    assert ce.BUDGET_ENFORCEMENT_DECISION_VALUES == tuple(
        get_args(ce.BudgetEnforcementDecision)
    )
    assert ce.SCOPE_KIND_VALUES == tuple(get_args(ce.ScopeKind))


def test_apply_cost_attribution_does_not_decide() -> None:
    ledger = _Ledger()
    attribution = ce.CostAttribution(
        action_id="a-1",
        cost_unit="tool_call",
        amount=1.0,
        charged_agent_id="agent-A",
        charged_session_id="session-S",
        source_owner="test",
    )
    returned = ce.apply_cost_attribution(attribution, ledger=ledger)
    assert returned is attribution
    assert ledger.debits == [attribution]
    assert isinstance(returned, ce.CostAttribution)
    assert not isinstance(returned, ce.BudgetEnforcementDecisionEvent)


def test_evaluate_budget_enforcement_does_not_debit() -> None:
    envelope = ce.QuotaEnvelope(
        scope_kind="session",
        scope_id="S-1",
        cost_unit="tool_call",
        remaining=5.0,
        limit=10.0,
        window_started_at="t0",
        window_ends_at="t1",
    )
    attribution = ce.CostAttribution(
        action_id="a-1",
        cost_unit="tool_call",
        amount=1.0,
        charged_agent_id="agent-A",
        charged_session_id="S-1",
        source_owner="test",
    )
    event = ce.evaluate_budget_enforcement(
        attribution,
        envelope=envelope,
        recorded_at="t-eval",
        budget_name="tool_call_per_session",
    )
    assert isinstance(event, ce.BudgetEnforcementDecisionEvent)
    assert envelope.remaining == 5.0


def test_attribution_records_are_immutable() -> None:
    attribution = ce.CostAttribution(
        action_id="a-1",
        cost_unit="tool_call",
        amount=1.0,
        charged_agent_id="agent-A",
        charged_session_id="session-S",
        source_owner="test",
    )
    with pytest.raises(Exception):  # ValidationError / TypeError on frozen
        attribution.amount = 99.0  # type: ignore[misc]


def test_envelope_records_are_immutable() -> None:
    envelope = ce.QuotaEnvelope(
        scope_kind="session",
        scope_id="S-1",
        cost_unit="tool_call",
        remaining=5.0,
        limit=10.0,
        window_started_at="t0",
        window_ends_at="t1",
    )
    with pytest.raises(Exception):
        envelope.remaining = 0.0  # type: ignore[misc]


def test_quota_envelope_survives_fresh_run_state() -> None:
    store = _DurableStore()
    initial = ce.QuotaEnvelope(
        scope_kind="session",
        scope_id="S-1",
        cost_unit="tool_call",
        remaining=42.0,
        limit=100.0,
        window_started_at="t0",
        window_ends_at="t-end",
    )
    store.put(initial)

    del initial

    reloaded = ce.load_quota_envelope("session", "S-1", "tool_call", store=store)
    assert reloaded.remaining == 42.0
    assert reloaded.limit == 100.0
    assert reloaded.scope_kind == "session"
    assert reloaded.scope_id == "S-1"
    assert reloaded.cost_unit == "tool_call"


def test_load_quota_envelope_is_pure_delegation() -> None:
    store = _DurableStore()
    with pytest.raises(KeyError):
        ce.load_quota_envelope("agent", "missing", "llm_call", store=store)


def test_attribution_chain_composes_across_delegation() -> None:
    parent = ce.project_action_to_cost_attribution(
        _Action(action_id="parent-1", cost_unit="a2a_call", amount=1.0),
        charged_agent_id="agent-root",
        charged_session_id="session-S",
        charged_mission_id="mission-M",
        source_owner="orchestrate.handler",
    )
    child = ce.project_action_to_cost_attribution(
        _Action(action_id="child-1", cost_unit="tool_call", amount=1.0),
        charged_agent_id="agent-child",
        charged_session_id="session-S",
        charged_mission_id="mission-M",
        parent_action_id=parent.action_id,
        source_owner="delegated.handler",
    )
    grandchild = ce.project_action_to_cost_attribution(
        _Action(action_id="grandchild-1", cost_unit="tool_call", amount=1.0),
        charged_agent_id="agent-grandchild",
        charged_session_id="session-S",
        charged_mission_id="mission-M",
        parent_action_id=child.action_id,
        source_owner="coding.subtasks.split",
    )

    chain: list[str] = [grandchild.action_id]
    by_id: dict[str, ce.CostAttribution] = {
        a.action_id: a for a in (parent, child, grandchild)
    }
    cursor: Optional[str] = grandchild.parent_action_id
    while cursor is not None:
        chain.append(cursor)
        cursor = by_id[cursor].parent_action_id

    assert chain == ["grandchild-1", "child-1", "parent-1"]


def test_attribution_chain_with_no_parent_is_root() -> None:
    root = ce.project_action_to_cost_attribution(
        _Action(action_id="r-1", cost_unit="tool_call", amount=1.0),
        charged_agent_id="agent-root",
        charged_session_id="session-S",
        source_owner="brain.state",
    )
    assert root.parent_action_id is None


@pytest.mark.parametrize(
    ("schema", "expected_fields"),
    [
        (
            ce.CostAttribution,
            {
                "action_id",
                "cost_unit",
                "amount",
                "charged_agent_id",
                "charged_session_id",
                "charged_mission_id",
                "parent_action_id",
                "source_owner",
            },
        ),
        (
            ce.BudgetEnforcementDecisionEvent,
            {
                "decision",
                "budget_name",
                "reason_code",
                "scope_kind",
                "attribution_ref",
                "recorded_at",
            },
        ),
        (
            ce.QuotaEnvelope,
            {
                "scope_kind",
                "scope_id",
                "cost_unit",
                "remaining",
                "limit",
                "window_started_at",
                "window_ends_at",
            },
        ),
    ],
)
def test_runtime_cost_schemas_exclude_prose_and_match_audit_section_6(
    schema, expected_fields: set[str]
) -> None:
    fields = set(schema.model_fields.keys())
    assert fields.isdisjoint(_FORBIDDEN_PROSE_FIELDS), fields & _FORBIDDEN_PROSE_FIELDS
    assert fields == expected_fields


def _envelope(remaining: float, limit: float = 100.0) -> ce.QuotaEnvelope:
    return ce.QuotaEnvelope(
        scope_kind="session",
        scope_id="S-1",
        cost_unit="tool_call",
        remaining=remaining,
        limit=limit,
        window_started_at="t0",
        window_ends_at="t1",
    )


def _attribution(amount: float) -> ce.CostAttribution:
    return ce.CostAttribution(
        action_id="a-1",
        cost_unit="tool_call",
        amount=amount,
        charged_agent_id="agent-A",
        charged_session_id="S-1",
        source_owner="test",
    )


def test_evaluate_hard_stop_when_amount_exceeds_remaining() -> None:
    event = ce.evaluate_budget_enforcement(
        _attribution(amount=2.0),
        envelope=_envelope(remaining=1.0),
        recorded_at="t",
        budget_name="tool_call_per_session",
    )
    assert event.decision == "hard_stop"
    assert event.reason_code == ce.REASON_CODE_QUOTA_EXHAUSTED
    assert event.scope_kind == "session"
    assert event.attribution_ref == "a-1"


def test_evaluate_allow_when_well_within_budget() -> None:
    event = ce.evaluate_budget_enforcement(
        _attribution(amount=1.0),
        envelope=_envelope(remaining=80.0, limit=100.0),
        recorded_at="t",
        budget_name="tool_call_per_session",
    )
    assert event.decision == "allow"
    assert event.reason_code == ce.REASON_CODE_WITHIN_BUDGET


def test_evaluate_warn_near_threshold() -> None:
    event = ce.evaluate_budget_enforcement(
        _attribution(amount=2.0),
        envelope=_envelope(remaining=11.0, limit=100.0),
        recorded_at="t",
        budget_name="tool_call_per_session",
    )
    assert event.decision == "warn"
    assert event.reason_code == ce.REASON_CODE_QUOTA_WARNING


def test_evaluate_is_deterministic() -> None:
    attribution = _attribution(amount=1.0)
    envelope = _envelope(remaining=50.0)
    a = ce.evaluate_budget_enforcement(
        attribution,
        envelope=envelope,
        recorded_at="t",
        budget_name="tool_call_per_session",
    )
    b = ce.evaluate_budget_enforcement(
        attribution,
        envelope=envelope,
        recorded_at="t",
        budget_name="tool_call_per_session",
    )
    assert a == b


def test_evaluate_rejects_mismatched_cost_unit() -> None:
    attribution = ce.CostAttribution(
        action_id="a-1",
        cost_unit="tool_call",
        amount=1.0,
        charged_agent_id="agent-A",
        charged_session_id="S-1",
        source_owner="test",
    )
    envelope = ce.QuotaEnvelope(
        scope_kind="session",
        scope_id="S-1",
        cost_unit="llm_call",  # mismatched
        remaining=10.0,
        limit=100.0,
        window_started_at="t0",
        window_ends_at="t1",
    )
    with pytest.raises(ValueError):
        ce.evaluate_budget_enforcement(
            attribution,
            envelope=envelope,
            recorded_at="t",
            budget_name="b",
        )


def test_project_action_requires_structural_fields() -> None:
    class _Broken:
        pass  # no action_id / cost_unit / amount

    with pytest.raises(TypeError):
        ce.project_action_to_cost_attribution(
            _Broken(),
            charged_agent_id="a",
            charged_session_id="s",
            source_owner="test",
        )


def test_project_action_passes_through_typed_fields() -> None:
    action = _Action(action_id="a-7", cost_unit="llm_call", amount=3.5)
    attribution = ce.project_action_to_cost_attribution(
        action,
        charged_agent_id="agent-A",
        charged_session_id="session-S",
        source_owner="brain.loop.orchestration",
    )
    assert attribution.action_id == "a-7"
    assert attribution.cost_unit == "llm_call"
    assert attribution.amount == 3.5
    assert attribution.source_owner == "brain.loop.orchestration"
