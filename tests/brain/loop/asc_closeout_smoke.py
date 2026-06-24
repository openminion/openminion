from __future__ import annotations

import json
import tempfile
from pathlib import Path

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


class _Runtime:
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
            output_text=(
                "Finished the consolidation-to-compaction handoff. "
                "Next: run the repo validations and capture the artifact."
            ),
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


class _SessionAPI:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append_event(self, session_id, event_type, payload, **kwargs) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": dict(payload),
                "kwargs": dict(kwargs),
            }
        )


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

    def __init__(self, active_state):
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


def run_closeout() -> Path:
    state = WorkingState(
        session_id="session-asc-closeout",
        agent_id="agent-1",
        goal="close the ASC tracker",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=100,
            time_ms=1000,
        ),
    )
    state.module_state = {
        "memory_context_maintenance": {
            "last_consolidation_marker": "2026-05-22T11:59:59+00:00",
        }
    }
    session_api = _SessionAPI()
    result = run_self_compaction_step(
        working_state=state,
        runtime=_Runtime(),
        model="gpt-4.2-mini",
        context_service=_EligibilityService(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        session_api=session_api,
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
    checks = {
        "eligibility_ok": bool(result.applied),
        "summary_emitted": bool(result.summary_text),
        "session_work_summary_updated": bool(state.session_work_summary),
        "compaction_marker_written": bool(
            state.module_state["memory_context_maintenance"].get(
                "last_compaction_marker"
            )
        ),
        "audit_event_queryable": bool(session_api.events),
        "summary_surfaces_in_next_prompt": "[SESSION WORK SUMMARY]" in rendered
        and bool(state.session_work_summary in rendered),
        "ordering_observed": (
            state.module_state["memory_context_maintenance"][
                "last_consolidation_marker"
            ]
            < state.module_state["memory_context_maintenance"]["last_compaction_marker"]
        ),
    }
    out_dir = (
        Path(tempfile.mkdtemp(prefix="asc-closeout-"))
        / ".openminion"
        / "runtime"
        / "asc-20260522-closeout"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "summary.json"
    out_path.write_text(
        json.dumps(
            {
                "checks": checks,
                "summary_text": state.session_work_summary,
                "event_count": len(session_api.events),
                "decision": "promote_to_qa" if all(checks.values()) else "blocked",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return out_path
