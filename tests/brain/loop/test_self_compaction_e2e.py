from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openminion.modules.brain.loop.self_compaction import run_self_compaction_step
from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.context.compress.eligibility import (
    CompactionBudgetState,
    DefaultCompactionEligibility,
)
from openminion.modules.context.schemas import (
    BuildPackRequest,
    IdentitySnippet,
    SessionSlice,
)
from openminion.modules.context.service import ContextCtlService
from openminion.modules.llm.schemas import LLMResponse


@dataclass
class _Runtime:
    output_text: str

    def complete(
        self,
        *,
        messages,
        tools,
        model,
        tool_choice,
        max_output_tokens,
        metadata,
    ) -> LLMResponse:
        del messages, tools, tool_choice, max_output_tokens, metadata
        return LLMResponse(
            ok=True,
            provider="fake",
            model=model,
            output_text=self.output_text,
            finish_reason="stop",
        )


class _EligibilityService:
    def __init__(self) -> None:
        self._checker = DefaultCompactionEligibility()

    def evaluate_self_compaction_eligibility(
        self, *, working_state, prompt_token_estimate, budget_state, now
    ):
        return self._checker.is_eligible(
            working_state,
            prompt_token_estimate=prompt_token_estimate,
            budget_state=budget_state,
            now=now,
        )


@dataclass
class _SessionAPI:
    events: list[dict[str, Any]] = field(default_factory=list)

    def append_event(self, session_id, event_type, payload, **kwargs) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": dict(payload),
                "kwargs": dict(kwargs),
            }
        )


def _state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        goal="finish the remaining tracker",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=100,
            time_ms=1000,
        ),
    )


def test_self_compaction_threshold_not_met_is_noop() -> None:
    state = _state()

    result = run_self_compaction_step(
        working_state=state,
        runtime=_Runtime(output_text="unused"),
        model="gpt-4.2-mini",
        context_service=_EligibilityService(),
        prompt_token_estimate=20,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        recent_work="short checkpoint",
    )

    assert result.applied is False
    assert result.reason_code == "BELOW_THRESHOLD"
    assert "memory_context_maintenance" not in state.module_state


def test_self_compaction_same_turn_refire_is_noop() -> None:
    state = _state()
    session_api = _SessionAPI()

    first = run_self_compaction_step(
        working_state=state,
        runtime=_Runtime(output_text="Checkpoint 1"),
        model="gpt-4.2-mini",
        context_service=_EligibilityService(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        session_api=session_api,
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        recent_work=("checkpoint " * 90).strip(),
    )
    second = run_self_compaction_step(
        working_state=state,
        runtime=_Runtime(output_text="Checkpoint 2"),
        model="gpt-4.2-mini",
        context_service=_EligibilityService(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        session_api=session_api,
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        recent_work=("checkpoint " * 90).strip(),
    )

    assert first.applied is True
    assert second.applied is False
    assert second.reason_code == "ALREADY_COMPACTED_THIS_TURN"
    assert state.session_work_summary == "Checkpoint 1"


class _IdentityClient:
    contract_version = "v1"

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity for {agent_id}",
        )


class _SliceSession:
    contract_version = "v1"

    def __init__(self, active_state: dict[str, Any]) -> None:
        self._active_state = active_state

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            summary_short="",
            active_state=self._active_state,
        )


class _MemoryClient:
    contract_version = "v1"

    def query_facts(self, **kwargs):
        del kwargs
        return []

    def query_memory_cards(self, **kwargs):
        del kwargs
        return []

    def recall_session_start_memory(self, **kwargs):
        del kwargs
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id):
        del procedure_id
        return None


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, **kwargs):
        del kwargs
        return []


def test_context_pack_surfaces_updated_session_work_summary_after_compaction() -> None:
    state = _state()
    run_self_compaction_step(
        working_state=state,
        runtime=_Runtime(
            output_text="Finished compaction integration. Next: verify prompt surfacing."
        ),
        model="gpt-4.2-mini",
        context_service=_EligibilityService(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        recent_work=("checkpoint " * 90).strip(),
    )
    service = ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SliceSession({"session_work_summary": state.session_work_summary}),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
    )

    pack = service.build_pack(
        BuildPackRequest(
            session_id=state.session_id,
            agent_id=state.agent_id,
            purpose="act",
            query="what next",
        )
    )

    rendered = "\n".join(segment.content for segment in pack.segments)
    assert "[SESSION WORK SUMMARY]" in rendered
    assert "Finished compaction integration" in rendered
