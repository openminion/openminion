from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.adapters.llm import _extract_structured_output
from openminion.modules.brain.schemas import DecisionAdapter


def test_open_decision_schema_accepts_registered_act_payload() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.93,
            "reason_code": "multi_time_lookup",
            "act_profile": "general",
            "execution_target": {"kind": "local"},
            "rationale": "Use the shared act loop to compare times.",
        }
    )

    assert decision.mode == "act"
    assert decision.act_profile == "general"
    assert decision.execution_target.kind == "local"
    assert decision.rationale == "Use the shared act loop to compare times."


def test_open_decision_schema_json_schema_includes_registered_payload_fields() -> None:
    schema = DecisionAdapter.json_schema()
    mode_schema = schema["properties"]["route"]
    assert mode_schema["enum"] == ["respond", "act"]
    assert "rationale" in schema["properties"]
    assert "act_profile" in schema["properties"]
    assert "execution_target" in schema["properties"]


def test_open_decision_schema_accepts_delegated_execution_target() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.88,
            "reason_code": "delegate_explicit_request",
            "act_profile": "general",
            "execution_target": {
                "kind": "delegated",
                "target_agent_id": "alibaba-kimi-k2-5",
                "expect_async": False,
            },
            "rationale": "The user explicitly requested delegated execution.",
        }
    )

    assert decision.mode == "act"
    assert decision.execution_target.kind == "delegated"
    assert decision.execution_target.target_agent_id == "alibaba-kimi-k2-5"


def test_open_decision_schema_rejects_submit_output_without_explicit_mode() -> None:
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                name="submit_output",
                arguments={
                    "confidence": 0.8,
                    "reason_code": "delegate",
                    "execution_target": {
                        "kind": "delegated",
                        "target_agent_id": "alibaba-kimi-k2-5",
                    },
                    "rationale": "The user explicitly requested delegation.",
                },
            )
        ],
        output_text="",
    )
    parsed = _extract_structured_output(response, DecisionAdapter)

    assert parsed is None
