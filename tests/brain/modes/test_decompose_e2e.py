from __future__ import annotations

from openminion.modules.tool.contracts.model_ids import (
    MODEL_GIT_ADD,
    MODEL_GIT_BRANCH,
    MODEL_GIT_COMMIT,
    MODEL_GIT_DIFF,
    MODEL_PLAN_COMPLETE,
    MODEL_PLAN_SET,
    MODEL_PLAN_UPDATE,
)
from openminion.modules.brain.execution.orchestrate.handler import (
    OrchestrateMode,
)
from openminion.modules.brain.loop import orchestration
from openminion.modules.brain.bootstrap.route_catalog import get_route_descriptor
from openminion.modules.brain.loop.entry import (
    build_entry_tool_specs,
    detect_entry_path,
)
from openminion.modules.brain.schemas import (
    ActDecision,
    ExecutionTargetPayload,
    RespondDecision,
)
from openminion.modules.brain.schemas.decisions import DecisionAdapter
from .test_decompose_integration import _ctx, _mode_result


def test_decompose_e2e_mock_pipeline_synthesizes_all_subtasks(monkeypatch) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"goal": "Research AWS pricing", "suggested_mode": "act"},
            {"goal": "Research GCP pricing", "suggested_mode": "act"},
            {"goal": "Research Azure pricing", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="aws",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["aws"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="gcp",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["gcp"],
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="azure",
                sub_intents=["azure"],
                answer="azure",
            ),
        ],
    )

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        label = str(getattr(decision, "reason_code", "") or "child")
        return _mode_result(state, f"provider:{label}")

    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: _fake_invoke(
            runner,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )
    ctx._services.runner.llm_api.answer = "AWS, GCP, and Azure were all compared."

    result = OrchestrateMode().execute(ctx)

    assert "AWS" in result.message
    assert "GCP" in result.message
    assert "Azure" in result.message
    assert len(result.action_result.outputs["subtask_results"]) == 3


def test_decompose_e2e_preserves_route_contract_and_existing_modes() -> None:
    schema = DecisionAdapter.json_schema()
    assert get_route_descriptor("decompose") is None
    assert "subtasks" in schema["properties"]
    decision = DecisionAdapter.validate_python(
        {
            "mode": "respond",
            "confidence": 0.9,
            "reason_code": "greeting",
            "respond_kind": "answer",
            "answer": "hi",
        }
    )
    assert decision.mode == "respond"


def test_decompose_control_tool_schema_is_model_visible_but_not_a_route() -> None:
    tool_specs, supports_seed = build_entry_tool_specs(
        None,
        act_profile="general",
        execution_target_kind="local",
    )
    by_name = {tool.name: tool for tool in tool_specs}

    assert supports_seed is True
    assert get_route_descriptor("decompose") is None
    assert "decompose" in by_name
    schema = by_name["decompose"].input_schema
    assert schema["required"] == ["subtasks"]
    subtask_schema = schema["properties"]["subtasks"]["items"]
    assert subtask_schema["required"] == ["id", "description"]
    assert "decompose_rationale" not in subtask_schema["properties"]


def test_general_entry_exposes_git_and_plan_runtime_tools_when_registered(
    monkeypatch,
) -> None:
    registered = [
        {"name": MODEL_GIT_BRANCH, "description": "Create or list branches."},
        {"name": MODEL_GIT_ADD, "description": "Stage changes."},
        {"name": MODEL_GIT_COMMIT, "description": "Commit staged changes."},
        {"name": MODEL_GIT_DIFF, "description": "Show diff output."},
        {"name": MODEL_PLAN_SET, "description": "Create the active plan."},
        {"name": MODEL_PLAN_UPDATE, "description": "Update an active plan item."},
        {
            "name": MODEL_PLAN_COMPLETE,
            "description": "Mark a plan item completed.",
        },
    ]
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.runtime.collect_runtime_tool_schemas",
        lambda _runner: registered,
    )

    tool_specs, supports_seed = build_entry_tool_specs(
        object(),
        act_profile="general",
        execution_target_kind="local",
    )
    by_name = {tool.name for tool in tool_specs}

    assert supports_seed is True
    assert {
        MODEL_GIT_BRANCH,
        MODEL_GIT_ADD,
        MODEL_GIT_COMMIT,
        MODEL_GIT_DIFF,
        MODEL_PLAN_SET,
        MODEL_PLAN_UPDATE,
        MODEL_PLAN_COMPLETE,
    }.issubset(by_name)
    assert "plan" in by_name
    assert "clarify" in by_name


def test_forced_dynamic_runtime_tool_specs_are_added_before_entry_filter(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestration,
        "build_runtime_tool_specs",
        lambda runner, *, allowed_tools: [
            type("Spec", (), {"name": name})() for name in sorted(allowed_tools)
        ],
    )

    specs = orchestration._with_forced_runtime_tool_specs(
        object(),
        [type("Spec", (), {"name": "clarify"})()],
        ["mcp.fixture.echo_text"],
    )

    assert [spec.name for spec in specs] == ["clarify", "mcp.fixture.echo_text"]
    assert orchestration._explicit_direct_tool_names_from_user_input(
        'tool mcp.fixture.echo_text {"text":"hi"}'
    ) == ["mcp.fixture.echo_text"]


def test_decompose_prose_without_tool_call_does_not_auto_invoke() -> None:
    response = type(
        "Response",
        (),
        {
            "output_text": "I should decompose this into research and implementation.",
            "assistant_messages": [],
            "tool_calls": [],
        },
    )()

    detection = detect_entry_path(response)

    assert detection.path == "respond"
    assert detection.tool_call_names == ()
