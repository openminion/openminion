from __future__ import annotations

import pytest

from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopError,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    canonical_tool_arguments,
    canonical_tool_batch_signature,
    canonical_tool_call_signature,
    resolve_allowed_tools,
    semantic_batch_signature,
)


def test_profile_requires_allowlist_for_explicit_exposure() -> None:
    with pytest.raises(AdaptiveToolLoopError):
        AdaptiveToolLoopProfile(
            profile_name="coding_v1",
            mode_name="coding",
            allowed_tools=None,
        )


def test_profile_rejects_allowlist_for_runtime_exposed_policy() -> None:
    with pytest.raises(AdaptiveToolLoopError):
        AdaptiveToolLoopProfile(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            tool_exposure_policy="runtime_exposed",
            allowed_tools=frozenset({"file.read"}),
        )


def test_resolve_allowed_tools_runtime_exposed_requires_runtime_surface() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="general_adaptive_v1",
        mode_name="act_adaptive",
        tool_exposure_policy="runtime_exposed",
        allowed_tools=None,
    )
    with pytest.raises(AdaptiveToolLoopError):
        resolve_allowed_tools(profile=profile, runtime_tool_names=[])


def test_canonical_tool_signature_distinguishes_argument_payloads() -> None:
    left = canonical_tool_call_signature(
        {"name": "file.read", "arguments": {"path": "a.py"}}
    )
    right = canonical_tool_call_signature(
        {"name": "file.read", "arguments": {"path": "b.py"}}
    )
    assert left != right


def test_canonical_tool_batch_signature_rejects_malformed_inputs() -> None:
    with pytest.raises(AdaptiveToolLoopError):
        canonical_tool_batch_signature("not-a-batch")
    with pytest.raises(AdaptiveToolLoopError):
        canonical_tool_arguments(["not", "a", "dict"])


def test_outcome_telemetry_payload_serializes_stable_contract() -> None:
    outcome = AdaptiveToolLoopOutcome(
        profile_name="coding_v1",
        mode_name="coding",
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=AdaptiveToolLoopState(
            iteration=2,
            llm_calls=2,
            tool_calls_made=["file.read"],
            total_tool_calls=1,
        ),
        allowed_tools=frozenset({"file.read"}),
        final_text="done",
    )
    payload = outcome.telemetry_payload()

    assert payload["adaptive.profile"] == "coding_v1"
    assert payload["adaptive.mode"] == "coding"
    assert payload["adaptive.termination_reason"] == "final_text"
    assert payload["adaptive.tool_calls_total"] == 1
    assert payload["adaptive.allowed_tools"] == ["file.read"]


def test_outcome_telemetry_payload_includes_aggregated_tool_results_when_present() -> (
    None
):
    outcome = AdaptiveToolLoopOutcome(
        profile_name="coding_v1",
        mode_name="coding",
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=AdaptiveToolLoopState(
            iteration=2,
            llm_calls=2,
            tool_calls_made=["file.read"],
            total_tool_calls=1,
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "tool_name": "file.read",
                        "ok": True,
                        "verified": True,
                        "content": "# README",
                        "error": "",
                        "data": {"path": "/tmp/README.md"},
                        "call_id": "cmd-1",
                        "source": "native",
                    }
                ]
            },
        ),
        allowed_tools=frozenset({"file.read"}),
        final_text="done",
    )

    payload = outcome.telemetry_payload()

    assert payload["tool_execution_count"] == 1
    assert payload["tool_calls_count"] == 1
    assert payload["tool_verified"] is True
    assert isinstance(payload["tool_results"], list)
    assert payload["tool_results"][0]["tool_name"] == "file.read"


# Correction profile fields


def test_profile_defaults_max_macro_corrections_zero() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="p", mode_name="m", allowed_tools=frozenset({"file.read"})
    )
    assert profile.max_macro_corrections == 0


def test_profile_defaults_macro_correction_cooldown_two() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="p", mode_name="m", allowed_tools=frozenset({"file.read"})
    )
    assert profile.macro_correction_cooldown == 2


def test_profile_defaults_reflection_model_none() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="p", mode_name="m", allowed_tools=frozenset({"file.read"})
    )
    assert profile.reflection_model is None


def test_profile_explicit_correction_fields() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="p",
        mode_name="m",
        allowed_tools=frozenset({"file.read"}),
        max_macro_corrections=3,
        macro_correction_cooldown=5,
        reflection_model="claude-3-5-haiku-20241022",
    )
    assert profile.max_macro_corrections == 3
    assert profile.macro_correction_cooldown == 5
    assert profile.reflection_model == "claude-3-5-haiku-20241022"


def test_correction_budget_exhausted_constant_is_string() -> None:
    assert isinstance(ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED, str)
    assert ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED == "correction_budget_exhausted"


def test_existing_profile_fields_still_work_after_new_fields() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="coding_v1",
        mode_name="coding",
        allowed_tools=frozenset({"file.read", "exec.run"}),
        max_iterations=10,
        reflection_policy="always",
    )
    assert profile.profile_name == "coding_v1"
    assert profile.mode_name == "coding"
    assert profile.max_iterations == 10
    assert profile.reflection_policy == "always"
    # New fields must have defaults
    assert profile.max_macro_corrections == 0
    assert profile.macro_correction_cooldown == 2
    assert profile.reflection_model is None
    assert profile.reflection_anomaly_threshold == 0.6


# semantic_batch_signature


def test_semantic_sig_same_args_different_key_order() -> None:
    sig1 = semantic_batch_signature(
        [{"name": "file.read", "arguments": {"path": "a.py", "encoding": "utf-8"}}]
    )
    sig2 = semantic_batch_signature(
        [{"name": "file.read", "arguments": {"encoding": "utf-8", "path": "a.py"}}]
    )
    assert sig1 == sig2


def test_semantic_sig_whitespace_stripped_in_values() -> None:
    sig1 = semantic_batch_signature(
        [{"name": "file.read", "arguments": {"path": "a.py"}}]
    )
    sig2 = semantic_batch_signature(
        [{"name": "file.read", "arguments": {"path": "  a.py  "}}]
    )
    assert sig1 == sig2


def test_semantic_sig_treats_null_like_placeholders_as_missing() -> None:
    sig1 = semantic_batch_signature([{"name": "weather", "arguments": {}}])
    sig2 = semantic_batch_signature(
        [{"name": "weather", "arguments": {"location": "None", "query": "  "}}]
    )
    assert sig1 == sig2


def test_semantic_sig_different_path_produces_different_signature() -> None:
    sig1 = semantic_batch_signature(
        [{"name": "file.read", "arguments": {"path": "a.py"}}]
    )
    sig2 = semantic_batch_signature(
        [{"name": "file.read", "arguments": {"path": "b.py"}}]
    )
    assert sig1 != sig2


def test_semantic_sig_rejects_non_iterable() -> None:
    with pytest.raises(AdaptiveToolLoopError):
        semantic_batch_signature("not-a-batch")


# ADAPTIVE_TERM_CIRCULAR_PATTERN constant


def test_circular_pattern_constant_is_string() -> None:
    assert isinstance(ADAPTIVE_TERM_CIRCULAR_PATTERN, str)
    assert ADAPTIVE_TERM_CIRCULAR_PATTERN == "circular_pattern"


# budget_conserve_threshold profile field


def test_profile_default_budget_conserve_threshold() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="p", mode_name="m", allowed_tools=frozenset({"file.read"})
    )
    assert profile.budget_conserve_threshold == 0.20


def test_profile_explicit_budget_conserve_threshold() -> None:
    profile = AdaptiveToolLoopProfile(
        profile_name="p",
        mode_name="m",
        allowed_tools=frozenset({"file.read"}),
        budget_conserve_threshold=0.10,
    )
    assert profile.budget_conserve_threshold == 0.10
