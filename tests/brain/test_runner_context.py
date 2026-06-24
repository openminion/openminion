from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock


from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    LLMProfiles,
    StepOutputEntry,
)
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    UsageInfo,
)


class DummyLogger:
    def emit(self, *args, **kwargs):
        return None


class _CapturingContextAdapter(LocalContextAdapter):
    def __init__(self, *, session_store) -> None:
        super().__init__(session_store=session_store)
        self.last_context: dict | None = None

    def build(self, **kwargs):  # type: ignore[override]
        context = super().build(**kwargs)
        self.last_context = context
        return context


class _StaticEntryLLM:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.request: LLMRequest | None = None

    def estimate_tokens(self, *, model: str, context: dict[str, object]) -> int:
        _ = model, context
        return 1

    def call(self, req: LLMRequest) -> LLMResponse:
        self.request = req
        return self.response


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=3,
        max_tool_calls=2,
        max_a2a_calls=1,
        max_total_llm_tokens=1000,
        max_elapsed_ms=10000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="test-agent",
        role="general",
        llm_profiles=llm_profiles,
        budgets=budgets,
        defaults=AgentDefaults(),
    )


def _build_runner(tmp_path: Path) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def test_context_build_preserves_turn_order_and_hints() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        session.append_turn("s-context", "user", "first")
        session.append_turn("s-context", "assistant", "second")
        session.append_turn("s-context", "user", "third")

        state = runner._load_or_init_state("s-context")
        context = runner._build_context(
            state=state,
            purpose="decide",
            budget={"tokens": 100},
            hints={"user_input": "hello"},
            logger=DummyLogger(),
        )

        turns = context.get("turns", [])
        assert [t["content"] for t in turns] == ["first", "second", "third"]
        assert context.get("hints", {}).get("user_input") == "hello"


def test_context_build_enforces_phase_hint_boundaries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _ = _build_runner(Path(tmp))
        state = runner._load_or_init_state("ctx-phase-boundaries")
        base_hints = {
            "_llm_call_id": "cid-1",
            "current_datetime": "2026-03-21T12:00:00",
            "user_input": "hello",
            "runtime_tool_schemas": [{"name": "weather"}],
            "step_history": [{"step_index": 1}],
            "closure_sub_intents": ["a"],
            "closure_intent_outcomes": [{"intent_id": "a", "status": "succeeded"}],
            "closure_success_criteria": {"done": True},
            "closure_action_summary": "ok",
            "closure_candidate_reason": "all_done",
            "command": {"kind": "tool"},
            "result": {"status": "success"},
            "feasibility_sub_intents": [{"id": "a", "description": "do_a"}],
            "feasibility_plan_steps": [{"command_id": "c1", "title": "step"}],
            "feasibility_runtime_facts": [
                {"tool_name": "weather", "kind": "auth_status"}
            ],
            "reflection_context_kind": "step_reflection",
            "reflection_goal_summary": "Finish the weather lookup accurately.",
            "reflection_plan_objective": "Finish the weather lookup accurately.",
            "reflection_plan_progress": "1/3",
            "reflection_step_context": {"step_index": 2, "total_steps": 3},
            "reflection_prior_outcomes": [{"step_index": 1, "summary": "seed"}],
            "reflection_full_step_history": [{"step_index": 1, "summary": "seed"}],
            "reflection_success_criteria": {"status": "success"},
            "prior_step_result": "prev",
            "output_key": "analysis",
            "style_overrides": {"x": "y"},
            "skill_hints": {"skill_id": "s1"},
            "raw_history": ["too much raw context"],
        }

        decide_ctx = runner._build_context(
            state=state,
            purpose="decide",
            budget={"max_tokens": 100},
            hints=dict(base_hints),
            logger=DummyLogger(),
        )
        decide_hints = decide_ctx.get("hints", {})
        assert "runtime_tool_schemas" in decide_hints
        assert "closure_sub_intents" not in decide_hints
        assert "command" not in decide_hints

        plan_ctx = runner._build_context(
            state=state,
            purpose="plan",
            budget={"max_tokens": 100},
            hints=dict(base_hints),
            logger=DummyLogger(),
        )
        plan_hints = plan_ctx.get("hints", {})
        assert "runtime_tool_schemas" in plan_hints
        assert "step_history" in plan_hints
        assert "closure_sub_intents" not in plan_hints
        assert "command" not in plan_hints

        act_ctx = runner._build_context(
            state=state,
            purpose="act",
            budget={"max_tokens": 100},
            hints=dict(base_hints),
            logger=DummyLogger(),
        )
        act_hints = act_ctx.get("hints", {})
        assert "prior_step_result" in act_hints
        assert "output_key" in act_hints
        assert "runtime_tool_schemas" not in act_hints
        assert "closure_sub_intents" not in act_hints

        judge_ctx = runner._build_context(
            state=state,
            purpose="judge",
            budget={"max_tokens": 100},
            hints=dict(base_hints),
            logger=DummyLogger(),
        )
        judge_hints = judge_ctx.get("hints", {})
        assert "closure_sub_intents" in judge_hints
        assert "closure_intent_outcomes" in judge_hints
        assert "closure_success_criteria" in judge_hints
        assert "closure_action_summary" in judge_hints
        assert "runtime_tool_schemas" not in judge_hints
        assert "command" not in judge_hints
        assert "result" not in judge_hints

        validate_ctx = runner._build_context(
            state=state,
            purpose="validate",
            budget={"max_tokens": 100},
            hints=dict(base_hints),
            logger=DummyLogger(),
        )
        validate_hints = validate_ctx.get("hints", {})
        assert "feasibility_sub_intents" in validate_hints
        assert "feasibility_plan_steps" in validate_hints
        assert "feasibility_runtime_facts" in validate_hints
        assert "closure_sub_intents" not in validate_hints
        assert "command" not in validate_hints

        reflect_ctx = runner._build_context(
            state=state,
            purpose="reflect",
            budget={"max_tokens": 100},
            hints=dict(base_hints),
            logger=DummyLogger(),
        )
        reflect_hints = reflect_ctx.get("hints", {})
        assert "command" in reflect_hints
        assert "result" in reflect_hints
        assert reflect_hints["reflection_context_kind"] == "step_reflection"
        assert reflect_hints["reflection_goal_summary"] == (
            "Finish the weather lookup accurately."
        )
        assert reflect_hints["reflection_plan_objective"] == (
            "Finish the weather lookup accurately."
        )
        assert reflect_hints["reflection_plan_progress"] == "1/3"
        assert reflect_hints["reflection_step_context"] == {
            "step_index": 2,
            "total_steps": 3,
        }
        assert reflect_hints["reflection_prior_outcomes"] == [
            {"step_index": 1, "summary": "seed"}
        ]
        assert reflect_hints["reflection_full_step_history"] == [
            {"step_index": 1, "summary": "seed"}
        ]
        assert reflect_hints["reflection_success_criteria"] == {"status": "success"}
        assert "raw_history" not in reflect_hints


def test_context_build_preserves_context_budget_tier_hint_when_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp))
        session.append_turn("ctx-budget-tier", "user", "first")

        state = runner._load_or_init_state("ctx-budget-tier")
        context = runner._build_context(
            state=state,
            purpose="decide",
            budget={"max_tokens": 200},
            hints={
                "user_input": "continue previous debugging",
                "context_budget_tier": "full",
            },
            logger=DummyLogger(),
        )

        assert context.get("hints", {}).get("context_budget_tier") == "full"
        manifest = context.get("context_manifest", {})
        assert manifest.get("context_budget_tier") == "full"


def test_decide_context_includes_current_datetime() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        context_api = _CapturingContextAdapter(session_store=session)
        llm = _StaticEntryLLM(
            LLMResponse(
                ok=True,
                provider="test",
                model="decide-default",
                output_text="ok",
                assistant_messages=[Message(role="assistant", content="ok")],
                tool_calls=[],
                usage=UsageInfo(total_tokens=1),
                finish_reason="stop",
            )
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=context_api,
            llm_api=llm,  # type: ignore[arg-type]
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("ctx-decide-hints")
        _ = runner._decide(
            state=state,
            user_input="what should we do now?",
            logger=MagicMock(),
        )

        hints = (context_api.last_context or {}).get("hints") or {}
        assert isinstance(hints, dict)
        current_datetime = str(hints.get("current_datetime") or "")
        assert current_datetime
        assert datetime.fromisoformat(current_datetime) is not None
        style_overrides = hints.get("style_overrides")
        assert isinstance(style_overrides, dict)
        for key in (
            "entry_response_rule",
            "entry_tool_rule",
            "entry_clarify_rule",
            "entry_no_routing_metadata_rule",
            "entry_text_answer_rule",
        ):
            assert str(style_overrides.get(key, "")).strip()
        assert "filename, path, location, or target details" in str(
            style_overrides.get("entry_clarify_rule", "")
        )
        assert "no blocking detail is missing" in str(
            style_overrides.get("entry_text_answer_rule", "")
        )
        assert "submit_output" in str(
            style_overrides.get("entry_no_routing_metadata_rule", "")
        )


def test_decide_normalizes_metadata_fields_from_llm_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        llm = _StaticEntryLLM(
            LLMResponse(
                ok=True,
                provider="test",
                model="decide-default",
                output_text="",
                assistant_messages=[Message(role="assistant", content="")],
                tool_calls=[
                    ToolCall(
                        name="browser",
                        arguments={"url": "https://google.com"},
                    )
                ],
                usage=UsageInfo(total_tokens=1),
                finish_reason="tool_calls",
            )
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=llm,  # type: ignore[arg-type]
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("ctx-decide-metadata-normalize")
        decision = runner._decide(
            state=state,
            user_input="open browser and go to google.com",
            logger=MagicMock(),
        )

        assert decision.mode == "act"
        assert getattr(decision, "_entry_response", None) is not None
        bootstrap_route = getattr(decision, "_pre_resolved_act_route", None)
        assert bootstrap_route is not None
        assert getattr(bootstrap_route, "act_profile", "") == "general"


def test_decide_replan_turn_uses_goal_as_query_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        context_api = _CapturingContextAdapter(session_store=session)
        llm = _StaticEntryLLM(
            LLMResponse(
                ok=True,
                provider="test",
                model="decide-default",
                output_text="ok",
                assistant_messages=[Message(role="assistant", content="ok")],
                tool_calls=[],
                usage=UsageInfo(total_tokens=1),
                finish_reason="stop",
            )
        )
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=context_api,
            llm_api=llm,  # type: ignore[arg-type]
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        state = runner._load_or_init_state("ctx-decide-replan-query")
        state.goal = "what's weather in SF?"
        state.step_outputs = [
            StepOutputEntry(
                step_index=0,
                command_id="cmd-weather",
                output_key="weather",
                summary="Weather: 16C in SF",
                outputs={"temperature_c": 16},
            )
        ]

        _ = runner._decide(
            state=state,
            user_input=None,
            logger=MagicMock(),
        )

        hints = (context_api.last_context or {}).get("hints") or {}
        assert hints.get("user_input") == "what's weather in SF?"
