from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    ActDecision,
    Decision,
    ExecutionTargetPayload,
    RespondDecision,
    FinishCommand,
    LLMProfiles,
    Plan,
    SubIntent,
    ToolCommand,
    build_sub_intent_id,
)
from openminion.modules.brain.bootstrap.validators import (
    validate_sub_intent_coverage,
    validate_success_criteria_coverage,
)
from tests.brain.runner_test_support import build_seeded_act_decision


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=8,
        max_tool_calls=4,
        max_a2a_calls=2,
        max_total_llm_tokens=4000,
        max_elapsed_ms=20000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="validator-agent",
        role="general",
        llm_profiles=llm_profiles,
        budgets=budgets,
        defaults=AgentDefaults(),
    )


def _browser_command(op: str) -> ToolCommand:
    return ToolCommand(
        title=f"browser:{op}",
        tool_name="browser",
        args={"op": op, "url": "https://example.com"},
        success_criteria={"status": "success"},
    )


def _weather_command(location: str = "San Francisco") -> ToolCommand:
    return ToolCommand(
        title=f"weather:{location}",
        tool_name="weather",
        args={"location": location},
        success_criteria={"status": "success"},
    )


def _seeded_act_decision(
    *,
    command: ToolCommand,
    reason_code: str,
    sub_intents: list[str],
    rationale: str = "",
) -> ActDecision:
    return build_seeded_act_decision(
        confidence=1.0,
        reason_code=reason_code,
        act_profile="general",
        execution_target={"kind": "local"},
        sub_intents=sub_intents,
        rationale=rationale,
        command=command,
    )


def test_sub_intent_coverage_catches_missing_browser_capability() -> None:
    decision = _seeded_act_decision(
        reason_code="test_missing_coverage",
        sub_intents=["start_browser", "navigate_to_url"],
        command=_browser_command("instance.start"),
    )
    failure = validate_sub_intent_coverage(
        decision=decision,
        commands=[decision._seeded_commands[0]],
    )
    assert failure is not None
    assert failure.code == "sub_intent_not_covered"
    assert "navigate_to_url" in failure.details.get("missing_sub_intents", [])


def test_sub_intent_coverage_passes_when_seeded_command_covers_multiple_intents() -> (
    None
):
    decision = _seeded_act_decision(
        reason_code="test_coverage_ok",
        sub_intents=["start_browser", "navigate_to_url"],
        command=_browser_command("tab.navigate"),
    )
    failure = validate_sub_intent_coverage(
        decision=decision,
        commands=[decision._seeded_commands[0]],
    )
    assert failure is None


def test_sub_intent_coverage_uses_provider_emitted_inputs_when_args_missing() -> None:
    command = ToolCommand(
        title="browser:navigate",
        tool_name="browser",
        inputs={"op": "tab.navigate", "url": "https://example.com"},
        success_criteria={"status": "success"},
    )
    decision = _seeded_act_decision(
        reason_code="provider_input_shape",
        sub_intents=["start_browser", "navigate_to_url"],
        command=command,
    )
    failure = validate_sub_intent_coverage(decision=decision, commands=[command])
    assert failure is None


def test_sub_intent_coverage_accepts_explicit_plan_step_refs_for_blocked_intents() -> (
    None
):
    decision = ActDecision(
        confidence=1.0,
        reason_code="partial_coverage_plan",
        act_profile="general",
        execution_target=ExecutionTargetPayload(kind="local"),
        sub_intents=["check_weather", "book_flight"],
    )
    commands = [
        ToolCommand(
            title="weather",
            tool_name="weather",
            args={"location": "Tokyo"},
            success_criteria={"status": "success"},
            sub_intent_ids=["check_weather"],
        ),
        FinishCommand(
            title="booking unavailable",
            final_message="Flight booking is not available.",
            success_criteria={},
            sub_intent_ids=["book_flight"],
        ),
    ]
    failure = validate_sub_intent_coverage(decision=decision, commands=commands)
    assert failure is None


def test_sub_intent_coverage_accepts_structured_id_refs_for_blocked_intents() -> None:
    decision = ActDecision(
        confidence=1.0,
        reason_code="partial_coverage_plan",
        act_profile="general",
        execution_target=ExecutionTargetPayload(kind="local"),
        sub_intents=["check_weather", "book_flight"],
    )
    structured = [
        SubIntent(
            id=build_sub_intent_id("check_weather", index=1),
            description="check_weather",
        ),
        SubIntent(
            id=build_sub_intent_id("book_flight", index=2),
            description="book_flight",
        ),
    ]
    commands = [
        ToolCommand(
            title="weather",
            tool_name="weather",
            args={"location": "Tokyo"},
            success_criteria={"status": "success"},
            sub_intent_ids=[structured[0].id],
        ),
        FinishCommand(
            title="booking unavailable",
            final_message="Flight booking is not available.",
            success_criteria={},
            sub_intent_ids=[structured[1].id],
        ),
    ]
    failure = validate_sub_intent_coverage(decision=decision, commands=commands)
    assert failure is None


def test_sub_intent_validator_skips_when_field_missing_for_backward_compat() -> None:
    decision = RespondDecision(
        confidence=1.0,
        reason_code="legacy_model_output",
        respond_kind="answer",
        answer="ok",
    )
    failure = validate_sub_intent_coverage(decision=decision, commands=[])
    assert failure is None


def test_sub_intent_coverage_passes_for_weather_aliases() -> None:
    decision = _seeded_act_decision(
        reason_code="weather_lookup",
        sub_intents=["get_weather", "check_weather"],
        command=_weather_command(),
    )
    failure = validate_sub_intent_coverage(
        decision=decision,
        commands=[decision._seeded_commands[0]],
    )
    assert failure is None


def test_success_criteria_validator_catches_unproducible_outputs() -> None:
    plan = Plan(
        objective="open browser",
        steps=[_browser_command("instance.start")],
        stop_conditions=["done"],
        assumptions=[],
        risk_summary="low",
        success_criteria={"final_url": "https://example.com"},
    )
    failure = validate_success_criteria_coverage(plan=plan, commands=plan.steps)
    assert failure is not None
    assert failure.code == "success_criteria_not_producible"
    assert "final_url" in failure.details.get("missing_success_criteria_keys", [])


def test_success_criteria_validator_ignores_semantic_plan_completion_labels() -> None:
    plan = Plan(
        objective="plan japan trip",
        steps=[
            ToolCommand(
                title="research destinations",
                tool_name="web.search",
                args={"query": "best places to visit in japan"},
                success_criteria={"status": "success"},
            )
        ],
        stop_conditions=["done"],
        assumptions=[],
        risk_summary="low",
        success_criteria={
            "itinerary_complete": "2-week itinerary created",
            "destinations_covered": "Major destinations researched",
        },
    )
    failure = validate_success_criteria_coverage(plan=plan, commands=plan.steps)
    assert failure is None


def test_validators_never_read_raw_user_input_from_command_inputs() -> None:
    class _Explosive:
        def __str__(self) -> str:  # pragma: no cover - defensive guard
            raise AssertionError("validator touched raw user_input")

    command = ToolCommand(
        title="browser:start",
        tool_name="browser",
        args={"op": "instance.start"},
        inputs={"user_input": _Explosive()},
        success_criteria={"status": "success"},
    )
    decision = _seeded_act_decision(
        reason_code="input_access_guard",
        sub_intents=["start_browser"],
        command=command,
    )
    assert validate_sub_intent_coverage(decision=decision, commands=[command]) is None


def test_seeded_act_runs_without_validation_redecide() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=None,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        first = _seeded_act_decision(
            reason_code="first_invalid",
            sub_intents=["start_browser", "navigate_to_url"],
            command=_browser_command("instance.start"),
        )
        second = _seeded_act_decision(
            reason_code="second_valid",
            sub_intents=["start_browser", "navigate_to_url"],
            command=_browser_command("tab.navigate"),
        )
        with patch.object(
            runner, "_decide", side_effect=[first, second]
        ) as decide_mock:
            with patch.object(
                runner, "_approve", side_effect=lambda **kwargs: kwargs["command"]
            ):
                with patch.object(
                    runner,
                    "_act",
                    return_value=(
                        ActionResult(
                            command_id="cmd-1",
                            status="success",
                            summary="done",
                        ),
                        None,
                    ),
                ):
                    output = runner.step(
                        session_id="s-validator-redecide",
                        user_input="open browser and go to example.com",
                        trace_id="t-validator-redecide",
                    )

        assert output.status in {"done", "waiting_user"}
        assert decide_mock.call_count == 1
        event_types = [
            event["type"] for event in session.list_events("s-validator-redecide")
        ]
        assert "brain.decide.validation_failed" not in event_types
        assert "brain.decide.validation_redecide" not in event_types


def test_seeded_act_validation_uses_attached_sub_intent_ids_before_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=None,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )

        def _search_decision(**_kwargs) -> Decision:
            return _seeded_act_decision(
                reason_code="simple_search_request",
                sub_intents=["search_ai_news"],
                rationale="Single search is sufficient.",
                command=ToolCommand(
                    title="Search latest AI news",
                    tool_name="web.search",
                    args={"query": "latest AI news", "count": 10},
                    success_criteria={"status": "success"},
                ),
            )

        with patch.object(
            runner, "_decide", side_effect=_search_decision
        ) as decide_mock:
            with patch.object(
                runner, "_approve", side_effect=lambda **kwargs: kwargs["command"]
            ):
                with patch.object(
                    runner,
                    "_act",
                    return_value=(
                        ActionResult(
                            command_id="cmd-search-1",
                            status="success",
                            summary="done",
                        ),
                        None,
                    ),
                ):
                    output = runner.step(
                        session_id="s-validator-search-single",
                        user_input="search for latest news about AI",
                        trace_id="t-validator-search-single",
                    )

        assert output.status in {"done", "waiting_user"}
        assert decide_mock.call_count == 1
        assert output.working_state.plan is None
        assert [item.id for item in output.working_state.decision_sub_intent_refs] == [
            build_sub_intent_id("search_ai_news", index=1)
        ]
        assert [
            item.intent_id for item in output.working_state.intent_execution_states
        ] == [build_sub_intent_id("search_ai_news", index=1)]
        event_types = [
            event["type"] for event in session.list_events("s-validator-search-single")
        ]
        assert "brain.decide.validation_failed" not in event_types


def test_seeded_act_validation_attaches_all_decision_sub_intents_to_seeded_command() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            llm_api=None,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )

        declared_sub_intents = ["get_latest_news", "iran_us_tensions"]
        expected_ids = [
            build_sub_intent_id("get_latest_news", index=1),
            build_sub_intent_id("iran_us_tensions", index=2),
        ]

        def _search_decision(**_kwargs) -> Decision:
            return _seeded_act_decision(
                reason_code="latest_news_search_request",
                sub_intents=list(declared_sub_intents),
                rationale="One search can retrieve the current news.",
                command=ToolCommand(
                    title="Search latest Iran US news",
                    tool_name="web.search",
                    args={"query": "latest Iran US news", "count": 10},
                    success_criteria={"status": "success"},
                ),
            )

        with patch.object(
            runner, "_decide", side_effect=_search_decision
        ) as decide_mock:
            with patch.object(
                runner, "_approve", side_effect=lambda **kwargs: kwargs["command"]
            ):
                with patch.object(
                    runner,
                    "_act",
                    return_value=(
                        ActionResult(
                            command_id="cmd-search-2",
                            status="success",
                            summary="done",
                        ),
                        None,
                    ),
                ):
                    output = runner.step(
                        session_id="s-validator-search-multi-intent",
                        user_input="what is latest news on iran/us war?",
                        trace_id="t-validator-search-multi-intent",
                    )

        assert output.status in {"done", "waiting_user"}
        assert decide_mock.call_count == 1
        assert output.working_state.plan is None
        assert [
            item.id for item in output.working_state.decision_sub_intent_refs
        ] == expected_ids
        assert [
            item.intent_id for item in output.working_state.intent_execution_states
        ] == expected_ids
        event_types = [
            event["type"]
            for event in session.list_events("s-validator-search-multi-intent")
        ]
        assert "brain.decide.validation_failed" not in event_types
