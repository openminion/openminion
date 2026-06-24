from __future__ import annotations

from openminion.modules.context.compress.policy import (
    PolicyResolver,
    DEFAULT_QUALITY_OVERRIDES,
)
from openminion.modules.context.compress.registry import MethodRegistry
from openminion.modules.context.compress.schemas import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionRequest,
    InputBlock,
)


def _make_request(
    quality_hint=None,
    method_main: str = "extractive.v1",
    method_prepass: str | None = None,
) -> CompressionRequest:
    return CompressionRequest(
        request_id="req-tier",
        query="test query",
        blocks=[
            InputBlock(
                block_id="b1",
                type="retrieval",
                text="some text",
                refs=["ref#1"],
                meta={},
            )
        ],
        budgets=CompressionBudgets(max_output_tokens_total=512),
        policy=CompressionPolicy(
            method_main=method_main,
            method_prepass=method_prepass,
        ),
        engine_version="2026-03-01",
        retrieval_quality_hint=quality_hint,
    )


class TestQualityTierPolicyMap:
    def test_good_tier_does_not_override_policy_main(self):
        registry = MethodRegistry()
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint="GOOD", method_main="extractive.v1")
        resolution = resolver.resolve(request)
        assert resolution.main_method == "extractive.v1"
        assert resolution.fallback_used is False

    def test_ok_tier_does_not_override_policy_main(self):
        registry = MethodRegistry()
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint="OK", method_main="extractive.v1")
        resolution = resolver.resolve(request)
        assert resolution.main_method == "extractive.v1"
        assert resolution.fallback_used is False

    def test_bad_tier_overrides_to_baseline(self):
        assert DEFAULT_QUALITY_OVERRIDES["BAD"] == MethodRegistry.BASELINE_METHOD_ID
        registry = MethodRegistry()
        resolver = PolicyResolver(registry)
        # use an unavailable main method so the BAD tier override matters
        registry.register_main("fancy.v1", available=False)
        request = _make_request(quality_hint="BAD", method_main="fancy.v1")
        resolution = resolver.resolve(request)
        assert resolution.main_method == "extractive.v1"

    def test_bad_tier_maps_to_baseline_in_registry(self):
        registry = MethodRegistry()
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint="BAD", method_main="extractive.v1")
        resolution = resolver.resolve(request)
        assert resolution.main_method == MethodRegistry.BASELINE_METHOD_ID

    def test_none_quality_hint_uses_policy_method(self):
        registry = MethodRegistry()
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint=None, method_main="extractive.v1")
        resolution = resolver.resolve(request)
        assert resolution.main_method == "extractive.v1"
        assert resolution.fallback_used is False

    def test_custom_quality_overrides_are_respected(self):
        registry = MethodRegistry()
        registry.register_main("custom.v1", available=True)
        resolver = PolicyResolver(registry, quality_overrides={"GOOD": "custom.v1"})
        request = _make_request(quality_hint="GOOD", method_main="custom.v1")
        resolution = resolver.resolve(request)
        assert resolution.main_method == "custom.v1"

    def test_prepass_unavailable_emits_warning(self):
        registry = MethodRegistry()
        registry.register_prepass("selective_context", available=False)
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint="GOOD", method_prepass="selective_context")
        resolution = resolver.resolve(request)
        assert resolution.prepass_method is None
        assert any("prepass_unavailable" in w for w in resolution.warnings)

    def test_fallback_used_is_false_for_available_policy_method(self):
        registry = MethodRegistry()
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint=None, method_main="extractive.v1")
        resolution = resolver.resolve(request)
        assert resolution.fallback_used is False

    def test_fallback_used_is_true_when_policy_method_unavailable(self):
        registry = MethodRegistry()
        registry.register_main("unavailable.v1", available=False)
        resolver = PolicyResolver(registry)
        request = _make_request(quality_hint=None, method_main="unavailable.v1")
        resolution = resolver.resolve(request)
        assert resolution.fallback_used is True
        assert resolution.main_method == "extractive.v1"
