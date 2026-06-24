from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.llm.providers.factory import RuntimeLLMHandle
from openminion.modules.llm.schemas import LLMResponse
from openminion.modules.memory.config import ConsolidationConfig
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.consolidation import (
    MAINTENANCE_MODULE_STATE_KEY,
    run_consolidation_cycle,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


def _state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=0,
            time_ms=0,
        ),
        session_work_summary="preserve me",
    )


def _candidate(*, candidate_id: str, created_at: str) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="session-1",
        proposed_scope="agent:agent-1",
        type="fact",
        title=f"title-{candidate_id}",
        content={"text": f"content-{candidate_id}"},
        source="validated",
        confidence=0.7,
        created_at=created_at,
        updated_at=created_at,
    )


class _KeepMergeClient:
    def complete(self, messages, tools=None, **overrides):  # noqa: ANN001
        del messages, tools, overrides
        return LLMResponse(
            ok=True,
            provider="echo",
            model="gpt-4.2-mini",
            output_text="keep current durable state",
            memory_consolidation={
                "decisions": [
                    {
                        "candidate_id": "cand-1",
                        "action": "keep",
                        "reasoning": "already represented",
                    }
                ]
            },
            assistant_messages=[],
            tool_calls=[],
        )


def _handle() -> RuntimeLLMHandle:
    return RuntimeLLMHandle(
        name="openai",
        model="gpt-4.2-mini",
        client=_KeepMergeClient(),
    )


def _later(marker: str, *, seconds: int = 1) -> str:
    return (datetime.fromisoformat(marker) + timedelta(seconds=seconds)).isoformat()


def test_run_consolidation_cycle_skips_when_idle_gate_fails() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    state = _state()
    store.candidate_put(_candidate(candidate_id="cand-1", created_at=now.isoformat()))

    result = run_consolidation_cycle(
        service,
        working_state=state,
        primary_model_handle=_handle(),
        config=ConsolidationConfig(idle_seconds_before_eligible=21600),
        target_scope="agent:agent-1",
        turn_id="turn-1",
        now=now,
    )

    assert result.applied is False
    assert result.reason_code == "IDLE_GATE_NOT_MET"
    assert MAINTENANCE_MODULE_STATE_KEY not in state.module_state
    assert state.session_work_summary == "preserve me"


def test_run_consolidation_cycle_skips_when_rate_limit_gate_fails() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=7)).isoformat()
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    state = _state()
    store.candidate_put(_candidate(candidate_id="cand-1", created_at=old))

    result = run_consolidation_cycle(
        service,
        working_state=state,
        primary_model_handle=_handle(),
        config=ConsolidationConfig(min_rate_limit_remaining_percent=25),
        target_scope="agent:agent-1",
        turn_id="turn-1",
        now=now,
        rate_limit_remaining_percent_probe=lambda _session_id, _agent_id: 10,
    )

    assert result.applied is False
    assert result.reason_code == "RATE_LIMIT_INSUFFICIENT"
    assert MAINTENANCE_MODULE_STATE_KEY not in state.module_state


def test_run_consolidation_cycle_is_idempotent_on_same_marker_hash() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=7)).isoformat()
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    state = _state()
    store.candidate_put(_candidate(candidate_id="cand-1", created_at=old))

    first = run_consolidation_cycle(
        service,
        working_state=state,
        primary_model_handle=_handle(),
        config=ConsolidationConfig(),
        target_scope="agent:agent-1",
        turn_id="turn-1",
        now=now,
        recent_rollout_probe=lambda _session_id, _agent_id, _limit: [
            service.candidate_get("cand-1")
        ],
    )
    second = run_consolidation_cycle(
        service,
        working_state=state,
        primary_model_handle=_handle(),
        config=ConsolidationConfig(),
        target_scope="agent:agent-1",
        turn_id="turn-2",
        now=now + timedelta(seconds=5),
        recent_rollout_probe=lambda _session_id, _agent_id, _limit: [
            service.candidate_get("cand-1")
        ],
    )

    assert first.applied is True
    assert second.applied is False
    assert second.reason_code == "ALREADY_CONSOLIDATED"
    assert (
        state.module_state[MAINTENANCE_MODULE_STATE_KEY]["last_consolidation_marker"]
        == now.isoformat()
    )


def test_run_consolidation_cycle_writes_marker_before_compaction_marker() -> None:
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=7)).isoformat()
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    state = _state()
    store.candidate_put(_candidate(candidate_id="cand-1", created_at=old))

    result = run_consolidation_cycle(
        service,
        working_state=state,
        primary_model_handle=_handle(),
        config=ConsolidationConfig(),
        target_scope="agent:agent-1",
        turn_id="turn-1",
        now=now,
        recent_rollout_probe=lambda _session_id, _agent_id, _limit: [
            service.candidate_get("cand-1")
        ],
    )

    maintenance = state.module_state[MAINTENANCE_MODULE_STATE_KEY]
    maintenance["last_compaction_marker"] = _later(
        maintenance["last_consolidation_marker"]
    )

    assert result.applied is True
    assert (
        maintenance["last_consolidation_marker"] < maintenance["last_compaction_marker"]
    )
    assert state.session_work_summary == "preserve me"
