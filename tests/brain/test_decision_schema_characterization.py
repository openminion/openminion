from __future__ import annotations

import pytest

from openminion.modules.brain.schemas import DecisionAdapter
from openminion.modules.brain.retry import build_decide_fail_closed_result


def test_decision_schema_characterization_accepts_core_modes() -> None:
    cases = (
        {
            "mode": "respond",
            "confidence": 0.9,
            "reason_code": "greeting",
            "respond_kind": "answer",
            "answer": "hi",
        },
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "lookup",
            "act_profile": "general",
            "execution_target": {"kind": "local"},
            "rationale": "Use the shared act loop to fetch the time.",
        },
        {
            "mode": "act",
            "confidence": 0.8,
            "reason_code": "compound_request",
            "act_profile": "general",
            "execution_target": {"kind": "local"},
        },
        {
            "mode": "respond",
            "confidence": 0.7,
            "reason_code": "missing_input",
            "respond_kind": "clarify",
            "question": "Which city?",
        },
    )
    for payload in cases:
        validated = DecisionAdapter.validate_python(payload)
        assert validated.mode == payload["mode"]
        if payload["mode"] == "respond":
            assert validated.respond_kind == payload.get("respond_kind", "answer")


def test_decision_schema_characterization_rejects_unregistered_modes() -> None:
    with pytest.raises(Exception):
        DecisionAdapter.validate_python(
            {
                "mode": "not_registered",
                "confidence": 0.5,
                "reason_code": "bad_mode",
            }
        )


# Gemini/GLM-style decide-schema failure characterization


def test_decision_schema_accepts_string_confidence_gemini_style() -> None:
    for raw_confidence, expected_range in (
        ("high", (0.85, 1.0)),
        ("medium", (0.5, 0.7)),
        ("low", (0.25, 0.4)),
    ):
        validated = DecisionAdapter.validate_python(
            {
                "mode": "respond",
                "confidence": raw_confidence,
                "reason_code": "test",
                "respond_kind": "answer",
                "answer": "hello",
            }
        )
        lo, hi = expected_range
        assert lo <= validated.confidence <= hi, (
            f"String confidence {raw_confidence!r} did not normalize to [{lo}, {hi}], "
            f"got {validated.confidence}"
        )


def test_decision_schema_accepts_comma_separated_sub_intents() -> None:
    from openminion.modules.brain.bootstrap.payloads import _normalize_sub_intents

    result = _normalize_sub_intents("search_web,get_time,summarize")
    assert result == ["search_web", "get_time", "summarize"]


def test_decision_schema_normalizes_empty_sub_intents() -> None:
    from openminion.modules.brain.bootstrap.payloads import _normalize_sub_intents

    assert _normalize_sub_intents(None) == []
    assert _normalize_sub_intents([]) == []
    assert _normalize_sub_intents("") == []


def test_decision_schema_repeated_tool_weather_two_cities_plan_mode() -> None:
    validated = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "multi_location_weather",
            "sub_intents": ["weather_new_york", "weather_london"],
            "act_profile": "general",
            "execution_target": {"kind": "local"},
        }
    )
    assert validated.mode == "act"
    assert len(validated.sub_intents) == 2


def test_decision_schema_accepts_act_with_many_sub_intents() -> None:
    validated = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.85,
            "reason_code": "compound_search_and_time",
            "sub_intents": ["web_search", "check_time"],
            "act_profile": "general",
            "execution_target": {"kind": "local"},
        }
    )
    assert validated.mode == "act"
    assert "web_search" in validated.sub_intents
    assert "check_time" in validated.sub_intents


def test_decision_schema_normalizes_legacy_decompose_subtask_wrappers() -> None:
    validated = DecisionAdapter.validate_python(
        {
            "mode": "plan",
            "confidence": 0.85,
            "reason_code": "compound_trip_plan",
            "plan_strategy": "decomposed",
            "subtasks": [
                {
                    "intent_id": "research",
                    "description": "Research current travel requirements",
                    "kind": "research",
                },
                {
                    "id": "1",
                    "goal": "Plan Tokyo days",
                    "subtasks": [{"id": "1.1", "goal": "Nested day detail"}],
                },
            ],
        }
    )

    assert validated.mode == "act"
    assert validated.act_profile == "orchestrate"
    assert validated.subtasks[0]["subtask_id"] == "research"
    assert validated.subtasks[0]["goal"] == "Research current travel requirements"
    assert "intent_id" not in validated.subtasks[0]
    assert "description" not in validated.subtasks[0]
    assert "kind" not in validated.subtasks[0]
    assert validated.subtasks[1]["subtask_id"] == "1"
    assert "id" not in validated.subtasks[0]
    assert "subtasks" not in validated.subtasks[1]


def test_decide_fail_closed_result_produces_respond() -> None:
    result = build_decide_fail_closed_result({})
    assert result["route"] == "respond"
    assert result["confidence"] == 0.3
    assert result["reason_code"] == "invalid_decide_structured_output"
    assert "answer" in result
    assert result["answer"]  # non-empty

    validated = DecisionAdapter.validate_python(result)
    assert validated.mode == "respond"


def test_decide_fail_closed_handles_tool_call_failure_kind() -> None:
    from openminion.modules.brain.retry import STRUCTURED_HAS_TOOL_CALLS_KEY

    result = build_decide_fail_closed_result({STRUCTURED_HAS_TOOL_CALLS_KEY: True})
    assert result["reason_code"] == "invalid_decide_tool_call"


# TURR-04 negative path: structurally invalid decisions are still rejected


def test_decision_schema_rejects_missing_mode() -> None:
    with pytest.raises(Exception):
        DecisionAdapter.validate_python(
            {
                "confidence": 0.9,
                "reason_code": "lookup",
            }
        )


def test_decision_schema_accepts_act_without_execution_target() -> None:
    validated = DecisionAdapter.validate_python(
        {
            "mode": "act",
            "confidence": 0.9,
            "reason_code": "lookup",
            "act_profile": "general",
        }
    )

    assert validated.mode == "act"
    assert validated.execution_target is None


def test_decision_schema_rejects_respond_clarify_without_question() -> None:
    with pytest.raises(Exception):
        DecisionAdapter.validate_python(
            {
                "mode": "respond",
                "confidence": 0.7,
                "reason_code": "missing",
                "respond_kind": "clarify",
            }
        )


def test_decision_schema_rejects_sub_intents_as_dicts() -> None:
    with pytest.raises(Exception):
        DecisionAdapter.validate_python(
            {
                "mode": "act",
                "confidence": 0.8,
                "reason_code": "compound",
                "act_profile": "general",
                "execution_target": {"kind": "local"},
                "sub_intents": [{"id": "si-1", "description": "search"}],
            }
        )
