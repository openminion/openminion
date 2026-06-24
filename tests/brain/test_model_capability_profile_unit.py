from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.adapters.llm import (
    LlmctlAdapter,
    _extract_structured_output,
)
from openminion.modules.brain.adapters.llm.model_profiles import (
    ModelCapabilityProfile,
    build_overrides_from_config,
    default_capability_profiles,
    resolve_capability_profile,
)
from openminion.modules.brain.retry import (
    STRUCTURED_FAILURE_KIND_KEY,
    STRUCTURED_RETRYABLE_KEY,
)
from openminion.modules.brain.schemas import DecisionAdapter
from openminion.modules.brain.runtime.context import build_context


def _decision_payload(
    *, mode: str = "respond", answer: str = "hello"
) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": mode,
        "confidence": 0.9,
        "reason_code": "test",
        "sub_intents": [],
        "rationale": "",
    }
    if mode == "respond":
        payload["respond_kind"] = "answer"
        payload["answer"] = answer
    elif mode == "ask_user":
        payload["mode"] = "respond"
        payload["respond_kind"] = "clarify"
        payload["question"] = "clarify?"
    return payload


def _response(
    *,
    tool_answer: str | None = None,
    body_answer: str | None = None,
) -> SimpleNamespace:
    tool_calls = []
    if tool_answer is not None:
        tool_calls.append(
            SimpleNamespace(
                name="submit_output",
                arguments=_decision_payload(answer=tool_answer),
            )
        )
    output_text = ""
    if body_answer is not None:
        output_text = json.dumps(_decision_payload(answer=body_answer))
    return SimpleNamespace(tool_calls=tool_calls, output_text=output_text)


def test_resolve_capability_profile_matches_gpt4_fragment() -> None:
    profile = resolve_capability_profile(model_name="gpt-4o-2024-08-06")
    assert profile.profile_id == "gpt4_default"


def test_resolve_capability_profile_matches_gpt5_fragment() -> None:
    profile = resolve_capability_profile(model_name="openai/gpt-5.4-nano-20260317")
    assert profile.profile_id == "gpt5_default"
    assert profile.decision_strategy == "full_schema"


def test_resolve_capability_profile_matches_claude_fragment() -> None:
    profile = resolve_capability_profile(model_name="claude-3-5-sonnet")
    assert profile.profile_id == "claude_default"


def test_resolve_capability_profile_matches_minimax_fragment() -> None:
    profile = resolve_capability_profile(model_name="minimax/minimax-m2.7")
    assert profile.profile_id == "minimax_default"
    assert profile.decision_strategy == "two_step_classify"
    assert profile.retry_strategy == "progressive_simplification"
    assert profile.retry_nudge_style == "json_body_first"


def test_resolve_capability_profile_matches_qwen_fragment() -> None:
    profile = resolve_capability_profile(model_name="qwen/qwen3.5-35b-a3b")
    assert profile.profile_id == "qwen_default"
    assert profile.decision_strategy == "two_step_classify"
    assert profile.retry_strategy == "progressive_simplification"
    assert profile.retry_nudge_style == "openai_function_calling"


def test_resolve_capability_profile_matches_glm_fragment() -> None:
    profile = resolve_capability_profile(model_name="glm-5")
    assert profile.profile_id == "glm_default"
    assert profile.decision_strategy == "two_step_classify"
    assert profile.retry_strategy == "progressive_simplification"
    assert profile.retry_nudge_style == "json_body_first"


def test_resolve_capability_profile_matches_kimi_fragment() -> None:
    profile = resolve_capability_profile(model_name="kimi-k2.5")
    assert profile.profile_id == "kimi_default"
    assert profile.decision_strategy == "two_step_classify"
    assert profile.retry_strategy == "progressive_simplification"
    assert profile.retry_nudge_style == "openai_function_calling"


def test_resolve_capability_profile_returns_fallback_for_unknown_model() -> None:
    profile = resolve_capability_profile(model_name="mystery-model")
    assert profile.profile_id == "fallback"


def test_resolve_capability_profile_is_case_insensitive() -> None:
    profile = resolve_capability_profile(model_name="GPT-4O-MINI")
    assert profile.profile_id == "gpt4_default"


def test_resolve_capability_profile_empty_model_name_returns_fallback() -> None:
    profile = resolve_capability_profile(model_name="")
    assert profile.profile_id == "fallback"


def test_default_capability_profiles_have_unique_ids() -> None:
    profile_ids = [profile.profile_id for profile in default_capability_profiles()]
    assert len(profile_ids) == len(set(profile_ids))


def test_build_overrides_from_config_merges_existing_profile() -> None:
    overrides = build_overrides_from_config(
        {"gpt4_default": {"max_structured_retries": 5}}
    )
    assert overrides[0].profile_id == "gpt4_default"
    assert overrides[0].max_structured_retries == 5


def test_build_overrides_from_config_creates_new_profile() -> None:
    overrides = build_overrides_from_config(
        {
            "custom_local": {
                "model_fragments": ["my-local-model"],
                "extraction_chain": ["json_body"],
            }
        }
    )
    assert overrides[0].profile_id == "custom_local"
    assert overrides[0].model_fragments == ("my-local-model",)
    assert overrides[0].extraction_chain == ("json_body",)


def test_build_overrides_from_config_skips_invalid_entries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    overrides = build_overrides_from_config(
        {"custom": {"retry_strategy": "same_schema"}}
    )
    assert overrides == ()
    assert "custom profiles must declare model_fragments" in caplog.text


def test_override_precedence_beats_default_profile() -> None:
    overrides = (
        ModelCapabilityProfile(
            profile_id="custom_gpt4",
            model_fragments=("gpt-4o",),
            extraction_chain=("json_body",),
        ),
    )
    profile = resolve_capability_profile(
        model_name="gpt-4o-2024-08-06",
        overrides=overrides,
    )
    assert profile.profile_id == "custom_gpt4"


def test_extract_structured_output_default_chain_prefers_tool_calls() -> None:
    parsed = _extract_structured_output(
        _response(tool_answer="tool", body_answer="body"),
        DecisionAdapter,
    )
    assert parsed["answer"] == "tool"


def test_extract_structured_output_json_body_first_skips_tool_calls() -> None:
    parsed = _extract_structured_output(
        _response(tool_answer="tool", body_answer="body"),
        DecisionAdapter,
        extraction_chain=("json_body", "tool_calls"),
    )
    assert parsed["answer"] == "body"


def test_extract_structured_output_tool_calls_only_skips_json_body() -> None:
    parsed = _extract_structured_output(
        _response(body_answer="body"),
        DecisionAdapter,
        extraction_chain=("tool_calls",),
    )
    assert parsed is None


def test_extract_structured_output_empty_chain_returns_none() -> None:
    parsed = _extract_structured_output(
        _response(body_answer="body"),
        DecisionAdapter,
        extraction_chain=(),
    )
    assert parsed is None


def test_extract_structured_output_unknown_strategy_names_are_skipped() -> None:
    parsed = _extract_structured_output(
        _response(body_answer="body"),
        DecisionAdapter,
        extraction_chain=("unknown", "json_body"),
    )
    assert parsed["answer"] == "body"


def test_extract_structured_output_rejects_missing_respond_answer() -> None:
    parsed = _extract_structured_output(
        SimpleNamespace(
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "respond",
                        "confidence": 0.9,
                        "reason_code": "greeting",
                        "sub_intents": ["greeting"],
                        "rationale": "",
                    },
                )
            ],
            output_text="",
        ),
        DecisionAdapter,
    )
    assert parsed is None


def test_llmctl_adapter_marks_missing_respond_answer_retryable_for_progressive_profile() -> (
    None
):
    client = MagicMock()
    client.call.return_value = SimpleNamespace(
        ok=True,
        error=SimpleNamespace(message=""),
        tool_calls=[],
        output_text=json.dumps(
            {
                "mode": "respond",
                "confidence": 0.9,
                "reason_code": "greeting",
                "sub_intents": ["greeting"],
                "rationale": "",
            }
        ),
    )
    adapter = LlmctlAdapter(client)

    result = adapter.call_structured(
        model="openai/gpt-5.4-nano-20260317",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result[STRUCTURED_RETRYABLE_KEY] is True
    assert result[STRUCTURED_FAILURE_KIND_KEY] == "invalid_decide_structured_output"


def test_llmctl_adapter_uses_context_override_for_extraction_chain() -> None:
    client = MagicMock()
    client.call.return_value = SimpleNamespace(
        ok=True,
        error=SimpleNamespace(message=""),
        tool_calls=[
            SimpleNamespace(
                name="submit_output",
                arguments=_decision_payload(answer="tool"),
            )
        ],
        output_text=json.dumps(_decision_payload(answer="body")),
    )
    adapter = LlmctlAdapter(client)

    result = adapter.call_structured(
        model="gpt-4o-2024-08-06",
        purpose="decide",
        context={
            "messages": [],
            "hints": {
                "model_capability_overrides": {
                    "gpt4_default": {"extraction_chain": ["json_body"]}
                }
            },
        },
        schema=DecisionAdapter,
    )

    assert result["answer"] == "body"


def test_build_context_injects_model_capability_overrides_into_hints() -> None:
    captured: dict[str, object] = {}

    class _ContextAPI:
        def build(self, **kwargs):
            captured.update(kwargs)
            return {}

    runner = SimpleNamespace(
        context_api=_ContextAPI(),
        profile=SimpleNamespace(
            model_capability_overrides={"gpt4_default": {"max_structured_retries": 5}}
        ),
        options=SimpleNamespace(
            outcome_attribution_config=SimpleNamespace(enabled=False)
        ),
        _validate_call_order=MagicMock(return_value={"valid": True, "reason": ""}),
        _emit_brain_operation=MagicMock(return_value=True),
    )
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        active_mode_name=None,
        gateway_system_context="",
        session_id="sess-1",
        agent_id="agent-1",
        trace_id="trace-1",
        decision_memory_refs=[],
        decision_context_pack_version=None,
        decision_context_recorded_at=None,
    )
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"max_tokens": 100},
        hints={"user_input": "hello"},
        logger=logger,
    )

    hints = captured.get("hints")
    assert isinstance(hints, dict)
    assert (
        hints["model_capability_overrides"]["gpt4_default"]["max_structured_retries"]
        == 5
    )
