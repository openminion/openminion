from __future__ import annotations

import json

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import BrainRunner, RunnerOptions
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    AutoFactExtractionConfig,
    LLMProfiles,
)


class _AFEIntegrationLLM(LocalLLMAdapter):
    def call_structured(self, *, model: str, purpose: str, context: dict, schema: type):
        if schema.__name__ == "UserMessageCandidateReport":
            return {
                "items": [
                    {
                        "kind": "fact",
                        "normalized_key": "fact:user_name",
                        "title": "user name",
                        "content": "Jay",
                        "tags": [],
                    },
                    {
                        "kind": "user_preference",
                        "normalized_key": "user_preference:language",
                        "title": "preferred language",
                        "content": "TypeScript",
                        "tags": [],
                    },
                ]
            }
        return super().call_structured(
            model=model,
            purpose=purpose,
            context=context,
            schema=schema,
        )


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="afe-int-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=5,
            max_tool_calls=3,
            max_a2a_calls=1,
            max_total_llm_tokens=1000,
            max_elapsed_ms=10_000,
        ),
        defaults=AgentDefaults(),
        auto_fact_extraction=AutoFactExtractionConfig(
            enabled=True,
            model_tier="reflect",
            max_items_per_turn=5,
            min_user_message_chars=5,
            initial_confidence=0.3,
        ),
    )


def _candidate_payloads(memory_api: LocalMemoryAdapter) -> list[dict]:
    rows = [
        json.loads(line)
        for line in memory_api.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [row for row in rows if row.get("kind") == "candidate"]


def test_brain_runner_afe_event_is_authoritative_for_prose_candidate_staging(
    tmp_path,
) -> None:
    session_api = LocalSessionStore(tmp_path / "sessions")
    memory_api = LocalMemoryAdapter(tmp_path / "memory")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session_api,
        context_api=LocalContextAdapter(session_store=session_api),
        llm_api=_AFEIntegrationLLM(),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=memory_api,
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )

    runner.step(
        session_id="s-afe-int",
        user_input="My name is Jay and I prefer TypeScript for backend work.",
        trace_id="trace-afe-int",
    )

    events = session_api.list_events("s-afe-int")
    completed = [
        event
        for event in events
        if str(event.get("type", "") or "") == "brain.auto_fact_extraction.completed"
    ]
    assert len(completed) == 1
    payload = dict(completed[0].get("payload", {}) or {})
    assert payload["extracted_items"] == 2
    assert payload["staged_candidates"] == 2
    assert payload["initial_confidence"] == 0.3

    candidates = _candidate_payloads(memory_api)
    assert len(candidates) == 2
    assert {row["record_type"] for row in candidates} == {"fact", "user_preference"}
    assert all(row["meta"]["source"] == "auto_extracted" for row in candidates)
