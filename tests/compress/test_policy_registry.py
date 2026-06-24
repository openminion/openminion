from typing import Optional

from openminion.modules.context.compress.policy import MethodResolution, PolicyResolver
from openminion.modules.context.compress.registry import MethodRegistry
from openminion.modules.context.compress.schemas import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionRequest,
    InputBlock,
)


def _sample_request(
    policy: CompressionPolicy,
    quality_hint: Optional[str] = None,
) -> CompressionRequest:
    blocks = [
        InputBlock(block_id="b1", type="retrieval", text="fact"),
    ]
    budgets = CompressionBudgets(max_output_tokens_total=256)
    return CompressionRequest(
        request_id="req-1",
        query="What happened?",
        blocks=blocks,
        budgets=budgets,
        policy=policy,
        engine_version="2026-03-01",
        retrieval_quality_hint=quality_hint,
    )


def test_policy_method_precedence_without_override():
    registry = MethodRegistry()
    registry.register_main("llmlingua2.v1")
    policy = CompressionPolicy(method_main="llmlingua2.v1")
    resolver = PolicyResolver(registry)

    result = resolver.resolve(_sample_request(policy))

    assert isinstance(result, MethodResolution)
    assert result.main_method == "llmlingua2.v1"
    assert result.fallback_used is False
    assert result.fallback_method == "extractive.v1"


def test_override_takes_priority_over_policy():
    registry = MethodRegistry()
    registry.register_main("recomp_extractive.v1")
    policy = CompressionPolicy(method_main="llmlingua2.v1")
    resolver = PolicyResolver(registry)

    result = resolver.resolve(
        _sample_request(policy),
        override_main="recomp_extractive.v1",
    )

    assert result.main_method == "recomp_extractive.v1"
    assert result.attempted_methods[0] == "recomp_extractive.v1"
    assert result.fallback_used is False


def test_quality_hint_override_applied_when_available():
    registry = MethodRegistry()
    registry.register_main("recomp_extractive.v1")
    policy = CompressionPolicy(method_main="llmlingua2.v1")
    resolver = PolicyResolver(
        registry,
        quality_overrides={"BAD": "recomp_extractive.v1"},
    )

    result = resolver.resolve(_sample_request(policy, quality_hint="BAD"))

    assert result.main_method == "recomp_extractive.v1"
    assert "recomp_extractive.v1" in result.attempted_methods
    assert result.fallback_used is True  # tier override counted as fallback usage


def test_unavailable_methods_trigger_fallback_chain():
    registry = MethodRegistry()
    registry.register_main("llmlingua2.v1", available=False)
    registry.register_main("longllmlingua.v1", available=False)
    policy = CompressionPolicy(
        method_main="llmlingua2.v1", fallback_method_id="longllmlingua.v1"
    )
    resolver = PolicyResolver(registry)

    result = resolver.resolve(_sample_request(policy))

    assert result.main_method == "extractive.v1"
    assert result.fallback_method == "extractive.v1"
    assert result.fallback_used is True
    assert set(result.unavailable_methods) == {"llmlingua2.v1", "longllmlingua.v1"}


def test_prepass_warning_when_unavailable():
    registry = MethodRegistry()
    policy = CompressionPolicy(method_prepass="selective_context")
    resolver = PolicyResolver(registry)

    result = resolver.resolve(_sample_request(policy))

    assert result.prepass_method is None
    assert result.warnings == ("prepass_unavailable:selective_context",)

    registry.register_prepass("selective_context")
    result_available = resolver.resolve(_sample_request(policy))
    assert result_available.prepass_method == "selective_context"
    assert result_available.warnings == ()
