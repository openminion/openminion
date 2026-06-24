from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import TypeAdapter

from openminion.modules.brain.adapters.llm.model_profiles import (
    DecisionStrategy,
    RetryStrategy,
    resolve_capability_profile,
)
from openminion.modules.brain.adapters.llm import _extract_structured_output
from openminion.modules.brain.bootstrap.validators import validate_sub_intent_coverage
from openminion.modules.brain.schemas import Command, Decision, DecisionAdapter
from openminion.modules.llm.providers.tool_choice import (
    should_retry_with_auto_tool_choice,
)

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "model_matrix"


def _fixture_path(family: str, case_id: str) -> Path:
    return _FIXTURE_ROOT / family / f"{case_id}.json"


def _load_fixture(family: str, case_id: str) -> dict:
    return json.loads(_fixture_path(family, case_id).read_text(encoding="utf-8"))


def _classify_provider_error(message: str) -> str:
    normalized = str(message or "").strip().lower()
    if "system message must be at the beginning" in normalized:
        return "system_message_ordering"
    if "input validation error" in normalized:
        return "input_validation"
    return "unknown"


def test_model_matrix_fixture_families_and_negative_paths_exist() -> None:
    expected = {
        "openai": {
            "basic_act_decision",
            "ambiguous_weather_canary",
            "empty_search_query_args",
            "invalid_submit_output_args",
            "json_body_decision",
            "simple_tool_closure_payload",
        },
        "gemini": {
            "covered_sub_intents",
            "string_tool_call_decision",
            "sub_intent_mismatch",
        },
        "minimax_glm": {"tool_choice_must_be_auto", "unrelated_bad_request"},
        "qwen": {"system_message_ordering", "input_validation_error"},
    }
    for family, case_ids in expected.items():
        loaded_case_ids = {
            path.stem for path in (_FIXTURE_ROOT / family).glob("*.json")
        }
        assert loaded_case_ids == case_ids
        negatives = {
            case_id
            for case_id in case_ids
            if not _load_fixture(family, case_id)["positive"]
        }
        assert negatives, f"{family} is missing a negative-path fixture"


def test_openai_characterization_accepts_json_body_decision_fixture() -> None:
    payload = _load_fixture("openai", "json_body_decision")
    response = SimpleNamespace(**payload["response"])
    parsed = _extract_structured_output(response, DecisionAdapter)
    assert parsed is not None
    assert parsed["mode"] == "respond"
    assert parsed["reason_code"] == "latest_news"


def test_openai_characterization_rejects_invalid_submit_output_fixture() -> None:
    payload = _load_fixture("openai", "invalid_submit_output_args")
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                name=item["name"],
                arguments=item["arguments"],
            )
            for item in payload["response"]["tool_calls"]
        ],
        output_text=payload["response"]["output_text"],
    )
    assert _extract_structured_output(response, DecisionAdapter) is None


def test_openai_characterization_accepts_basic_act_decision_fixture() -> None:
    payload = _load_fixture("openai", "basic_act_decision")
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                name=item["name"],
                arguments=item["arguments"],
            )
            for item in payload["response"]["tool_calls"]
        ],
        output_text=payload["response"]["output_text"],
        provider="openrouter",
        model=payload["model_name"],
        session_id="fixture",
    )
    parsed = _extract_structured_output(response, DecisionAdapter)
    assert parsed is not None
    assert parsed["mode"] == "act"
    assert parsed["reason_code"] == "time_now"
    assert parsed["act_profile"] == "general"
    assert parsed["execution_target"]["kind"] == "local"
    assert parsed["sub_intents"] == ["time_now"]


def test_openai_characterization_freezes_simple_tool_closure_payload_fixture() -> None:
    payload = _load_fixture("openai", "simple_tool_closure_payload")
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                name=item["name"],
                arguments=item["arguments"],
            )
            for item in payload["response"]["tool_calls"]
        ],
        output_text=payload["response"]["output_text"],
        provider="openrouter",
        model=payload["model_name"],
        session_id="fixture",
    )
    assert _extract_structured_output(response, DecisionAdapter) is None


def test_openai_characterization_accepts_empty_search_query_args_fixture() -> None:
    payload = _load_fixture("openai", "empty_search_query_args")
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                name=item["name"],
                arguments=item["arguments"],
            )
            for item in payload["response"]["tool_calls"]
        ],
        output_text=payload["response"]["output_text"],
        provider="openrouter",
        model=payload["model_name"],
        session_id="fixture",
    )
    parsed = _extract_structured_output(response, DecisionAdapter)
    assert parsed is not None
    assert parsed["mode"] == "act"
    assert parsed["reason_code"] == "search_news"
    assert parsed["act_profile"] == "general"
    assert parsed["execution_target"]["kind"] == "local"
    assert parsed["sub_intents"] == ["search_news"]


def test_openai_characterization_freezes_ambiguous_weather_negative_fixture() -> None:
    payload = _load_fixture("openai", "ambiguous_weather_canary")
    assert payload["positive"] is False
    assert payload["capability_category"] == "weather"
    assert payload["expected_reason_code"] == "weather_location_required"


def test_gemini_characterization_accepts_covered_sub_intents_fixture() -> None:
    payload = _load_fixture("gemini", "covered_sub_intents")
    decision = Decision.model_validate(payload["decision"])
    command_adapter = TypeAdapter(Command)
    commands = [command_adapter.validate_python(item) for item in payload["commands"]]
    assert validate_sub_intent_coverage(decision=decision, commands=commands) is None


def test_gemini_characterization_parses_string_tool_call_fixture() -> None:
    payload = _load_fixture("gemini", "string_tool_call_decision")
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                name=item["name"],
                arguments=item["arguments"],
            )
            for item in payload["response"]["tool_calls"]
        ],
        output_text=payload["response"]["output_text"],
        provider=payload["response"]["provider"],
        model=payload["response"]["model"],
        session_id=payload["response"]["session_id"],
    )
    parsed = _extract_structured_output(response, DecisionAdapter)
    assert parsed is not None
    assert parsed["mode"] == "act"
    assert parsed["reason_code"] == "one_step_search"
    assert parsed["act_profile"] == "general"
    assert parsed["execution_target"]["kind"] == "local"
    assert parsed["sub_intents"] == ["web_search", "summarize"]


def test_gemini_characterization_freezes_sub_intent_mismatch_fixture() -> None:
    payload = _load_fixture("gemini", "sub_intent_mismatch")
    decision = Decision.model_validate(payload["decision"])
    command_adapter = TypeAdapter(Command)
    commands = [command_adapter.validate_python(item) for item in payload["commands"]]
    failure = validate_sub_intent_coverage(decision=decision, commands=commands)
    assert failure is not None
    assert failure.code == "sub_intent_not_covered"
    assert failure.details["missing_sub_intents"] == ["web_search"]


def test_minimax_glm_characterization_matches_auto_tool_choice_retry_fixture() -> None:
    payload = _load_fixture("minimax_glm", "tool_choice_must_be_auto")
    error = SimpleNamespace(**payload["error"])
    assert should_retry_with_auto_tool_choice(error, payload["tool_choice"]) is True


def test_minimax_glm_characterization_rejects_unrelated_bad_request_fixture() -> None:
    payload = _load_fixture("minimax_glm", "unrelated_bad_request")
    error = SimpleNamespace(**payload["error"])
    assert should_retry_with_auto_tool_choice(error, payload["tool_choice"]) is False


def test_qwen_characterization_freezes_provider_error_signatures() -> None:
    ordering = _load_fixture("qwen", "system_message_ordering")
    validation = _load_fixture("qwen", "input_validation_error")
    assert (
        _classify_provider_error(ordering["error"]["message"])
        == ordering["expected_signature"]
    )
    assert (
        _classify_provider_error(validation["error"]["message"])
        == validation["expected_signature"]
    )


def test_mixed_family_capability_resolution_stays_family_owned() -> None:
    minimax = resolve_capability_profile(model_name="MiniMax-M2.5")
    qwen = resolve_capability_profile(model_name="qwen3.5-plus")
    glm = resolve_capability_profile(model_name="glm-5")
    kimi = resolve_capability_profile(model_name="kimi-k2.5")

    assert minimax.profile_id == "minimax_default"
    assert minimax.decision_strategy == DecisionStrategy.TWO_STEP_CLASSIFY

    assert qwen.profile_id == "qwen_default"
    assert qwen.decision_strategy == DecisionStrategy.TWO_STEP_CLASSIFY
    assert qwen.retry_strategy == RetryStrategy.PROGRESSIVE_SIMPLIFICATION

    assert glm.profile_id == "glm_default"
    assert glm.decision_strategy == DecisionStrategy.TWO_STEP_CLASSIFY
    assert glm.retry_strategy == RetryStrategy.PROGRESSIVE_SIMPLIFICATION
    assert kimi.profile_id == "kimi_default"
    assert kimi.decision_strategy == DecisionStrategy.TWO_STEP_CLASSIFY
    assert kimi.retry_strategy == RetryStrategy.PROGRESSIVE_SIMPLIFICATION
