from __future__ import annotations

from openminion.modules.brain.bootstrap.route_catalog import decision_visible_routes
from openminion.modules.brain.schemas import DecisionAdapter, ExecutionTargetPayload


def _local_target_payload() -> dict[str, object]:
    return ExecutionTargetPayload(kind="local").model_dump(mode="json")


def test_decision_schema_exposes_only_intent_first_top_level_modes() -> None:
    mode_enum = DecisionAdapter.flat_json_schema()["properties"]["route"]["enum"]
    assert mode_enum == ["respond", "act"]
    assert decision_visible_routes() == ["act", "respond"]


def test_decision_adapter_accepts_intent_first_payloads() -> None:
    respond = DecisionAdapter.validate_python(
        {
            "mode": "respond",
            "confidence": 0.9,
            "reason_code": "answer",
            "respond_kind": "answer",
            "answer": "hello",
        }
    )
    clarify = DecisionAdapter.validate_python(
        {
            "mode": "respond",
            "confidence": 0.9,
            "reason_code": "clarify",
            "respond_kind": "clarify",
            "question": "Which city?",
        }
    )
    act = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "shared_act_loop",
            "act_profile": "general",
            "execution_target": _local_target_payload(),
            "rationale": "Use the shared act loop to get the time.",
        }
    )
    orchestrate = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "compound_request",
            "act_profile": "orchestrate",
            "execution_target": _local_target_payload(),
            "rationale": "Decompose and orchestrate sub-tasks.",
            "subtasks": [{"subtask_id": "sub-1", "goal": "inspect"}],
        }
    )

    assert respond.mode == "respond"
    assert respond.respond_kind == "answer"
    assert clarify.mode == "respond"
    assert clarify.respond_kind == "clarify"
    assert act.mode == "act"
    assert act.act_profile == "general"
    assert orchestrate.mode == "act"
    assert orchestrate.act_profile == "orchestrate"


def test_decision_adapter_flattens_nested_branch_payloads() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "shared_loop",
            "act": {
                "act_profile": "general",
                "execution_target": _local_target_payload(),
                "rationale": "Use the shared act loop to complete the work.",
            },
        }
    )

    assert decision.mode == "act"
    assert decision.act_profile == "general"
    assert decision.rationale == "Use the shared act loop to complete the work."
