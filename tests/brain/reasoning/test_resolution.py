from __future__ import annotations

from openminion.modules.brain.runtime.reasoning import (
    ThinkingRequest,
    ThinkingResolutionInput,
    build_runtime_thinking_diagnostics,
    resolve_thinking,
)


def test_runtime_thinking_precedence_falls_through_layers() -> None:
    diagnostics = build_runtime_thinking_diagnostics(
        code_default_profile="minimal",
        system_profile="detailed",
        agent_profile="off",
        invocation_requested_profile="minimal",
        provider_name="openai",
        model_name="MiniMax-M2.5",
    )

    assert diagnostics.code_default_profile == "minimal"
    assert diagnostics.system_profile == "detailed"
    assert diagnostics.agent_profile == "off"
    assert diagnostics.invocation_requested_profile == "minimal"
    assert diagnostics.effective.reasoning_profile == "minimal"
    assert diagnostics.effective.source_layer == "invocation_override"


def test_unknown_requested_profile_degrades_to_minimal_with_reason() -> None:
    resolved = resolve_thinking(
        request=ThinkingRequest(
            purpose="unit_test",
            requested_profile="turbo-deliberate",
            provider="openai",
            model="MiniMax-M2.5",
        ),
        layers=ThinkingResolutionInput(code_default_profile="minimal"),
    )

    assert resolved.reasoning_profile == "minimal"
    assert resolved.degraded_reason == "unknown_reasoning_profile_normalized"
    assert resolved.diagnostics_payload()["degraded_reason"] == (
        "unknown_reasoning_profile_normalized"
    )


def test_provider_unsupported_suppresses_provider_effort_but_keeps_profile() -> None:
    resolved = resolve_thinking(
        request=ThinkingRequest(
            purpose="unit_test",
            requested_profile="detailed",
            provider="unsupported-provider",
            model="example-model",
        ),
        layers=ThinkingResolutionInput(code_default_profile="minimal"),
    )

    assert resolved.reasoning_profile == "detailed"
    assert resolved.provider_effort is None
    assert resolved.supported is False
    assert resolved.degraded_reason == "provider_effort_unsupported"


def test_request_profile_can_arrive_through_layer_input() -> None:
    resolved = resolve_thinking(
        request=ThinkingRequest(
            purpose="unit_test",
            requested_profile=None,
            provider="openai",
            model="MiniMax-M2.5",
        ),
        layers=ThinkingResolutionInput(
            code_default_profile="off",
            system_profile="minimal",
            request_profile="detailed",
        ),
    )

    assert resolved.reasoning_profile == "detailed"
    assert resolved.source_layer == "invocation_override"
