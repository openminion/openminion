from __future__ import annotations

from openminion.modules.brain.execution.orchestrate.handler import (
    OrchestrateMode,
)
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.bootstrap.route_catalog import get_route_descriptor
from openminion.modules.brain.schemas import (
    AgentCommand,
    ExecutionTargetPayload,
    RespondDecision,
)
from openminion.modules.brain.schemas.decisions import DecisionAdapter
from .test_delegate_integration import _ctx as _delegate_ctx
from .test_decompose_integration import _ctx as _decompose_ctx
from .test_decompose_integration import _mode_result


def test_delegate_e2e_mock_pipeline_returns_delegate_result() -> None:
    ctx, _services, _logger = _delegate_ctx(
        registry_data={"agent.weather": {"state": "healthy"}}
    )

    result = DelegateMode().execute(ctx)

    assert result.status == "done"
    assert "Delegate completed cleanly." in str(result.message)
    assert result.action_result is not None
    assert result.action_result.status == "success"


def test_delegate_e2e_decompose_subtask_can_resolve_to_delegate(monkeypatch) -> None:
    ctx, _runner, _services = _decompose_ctx(
        subtasks=[
            {"goal": "Ask weather agent", "suggested_mode": "act"},
            {"goal": "Summarize result", "suggested_mode": "respond"},
        ],
        decisions=[
            (
                lambda decision: (
                    decision._seeded_commands.append(
                        AgentCommand(
                            title="delegate weather",
                            target_agent_id="agent.weather",
                            method="act",
                            params={"goal": "check the weather"},
                        )
                    )
                    or decision
                )
            )(
                DecisionAdapter.validate_python(
                    {
                        "mode": "act",
                        "confidence": 0.9,
                        "reason_code": "delegate_weather",
                        "act_profile": "general",
                        "execution_target": {
                            "kind": "delegated",
                            "target_agent_id": "agent.weather",
                        },
                        "rationale": "Delegate the weather lookup.",
                    }
                )
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="summarize_delegate",
                sub_intents=["summary"],
                answer="done",
            ),
        ],
    )
    invoked_modes: list[str] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        invoked_modes.append(str(getattr(decision, "mode", "") or ""))
        return _mode_result(state, f"ran:{decision.mode}")

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
    ctx._services.runner.llm_api.answer = "Delegated weather plus summary."

    result = OrchestrateMode().execute(ctx)

    assert invoked_modes == ["act", "respond"]
    assert result.status == "done"
    assert "Delegated weather" in str(result.message)


def test_decision_schema_keeps_delegate_fields_hidden_after_cutover() -> None:
    schema = DecisionAdapter.json_schema()
    assert get_route_descriptor("delegate") is None
    assert "target_agent_id" not in schema["properties"]
    assert "goal" not in schema["properties"]
    decision = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "delegate_work",
            "act_profile": "general",
            "execution_target": {
                "kind": "delegated",
                "target_agent_id": "agent.weather",
            },
            "rationale": "Delegate the weather lookup.",
        }
    )
    assert decision.mode == "act"
    assert isinstance(decision.execution_target, ExecutionTargetPayload)
