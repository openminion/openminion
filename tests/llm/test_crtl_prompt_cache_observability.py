from __future__ import annotations

from openminion.cli.status.token_usage import (
    TokenUsageSnapshot,
    TokenUsageTotals,
    accumulate_usage,
    build_token_usage_snapshot,
    format_token_usage_summary,
    usage_totals_from_mapping,
)
from openminion.modules.context.prefix import PrefixCacheAdapter
from openminion.modules.llm.prompt_cache import (
    build_prompt_cache_observation_payload,
)
from openminion.modules.llm.providers.message_payloads import (
    _usage_from_anthropic,
    _usage_from_openai_like,
)
from openminion.modules.llm.schemas import UsageInfo


def test_prefix_cache_adapter_emits_openai_cache_control():
    adapter = PrefixCacheAdapter(provider="openai")
    blocks = adapter.cache_control_blocks(prefix_hash="abc123")
    assert blocks["cache_control"] == "auto"
    assert blocks["prefix_hash"] == "abc123"


def test_prefix_cache_adapter_emits_anthropic_cache_control():
    adapter = PrefixCacheAdapter(provider="anthropic")
    blocks = adapter.cache_control_blocks(prefix_hash="abc123")
    assert blocks["cache_control"] == {"type": "ephemeral"}
    assert blocks["prefix_hash"] == "abc123"


def test_prefix_cache_adapter_generic_is_neutral_no_op():
    adapter = PrefixCacheAdapter(provider="generic")
    blocks = adapter.cache_control_blocks(prefix_hash="abc")
    assert "cache_control" not in blocks
    assert blocks == {"prefix_hash": "abc"}


def test_usage_from_openai_like_without_prompt_tokens_details_returns_none():
    payload = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    usage = _usage_from_openai_like(payload)
    assert usage.cached_tokens is None
    assert usage.cache_creation_tokens is None


def test_usage_from_anthropic_without_cache_fields_returns_none():
    payload = {"input_tokens": 100, "output_tokens": 30}
    usage = _usage_from_anthropic(payload)
    assert usage.cached_tokens is None
    assert usage.cache_creation_tokens is None


def test_token_usage_totals_default_cached_tokens_is_none():
    t = TokenUsageTotals()
    assert t.cached_tokens is None
    assert t.is_empty is True


def test_usage_totals_from_mapping_returns_none_when_no_cache_field():
    totals = usage_totals_from_mapping({"prompt_tokens": 50, "completion_tokens": 10})
    assert totals is not None
    assert totals.cached_tokens is None


def test_observation_payload_supported_false_for_no_cache_data():
    usage = UsageInfo(input_tokens=100, output_tokens=20, total_tokens=120)
    payload = build_prompt_cache_observation_payload(
        provider="openai", model="MiniMax-M2.7", usage=usage
    )
    assert payload["supported"] is False
    assert "cached_tokens" not in payload


def test_observation_payload_with_none_usage():
    usage = None
    payload = build_prompt_cache_observation_payload(
        provider="openai", model="m", usage=usage
    )
    assert payload["supported"] is False
    assert "cached_tokens" not in payload


def test_usage_from_openai_like_extracts_cached_tokens_from_details():
    payload = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 64},
    }
    usage = _usage_from_openai_like(payload)
    assert usage.cached_tokens == 64
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20


def test_usage_from_openai_like_handles_malformed_details_gracefully():
    p1 = {"prompt_tokens": 50, "prompt_tokens_details": "not-a-dict"}
    p2 = {"prompt_tokens": 50, "prompt_tokens_details": {"cached_tokens": "many"}}
    assert _usage_from_openai_like(p1).cached_tokens is None
    assert _usage_from_openai_like(p2).cached_tokens is None


def test_usage_from_anthropic_extracts_cache_read_input_tokens():
    payload = {
        "input_tokens": 100,
        "output_tokens": 30,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 20,
    }
    usage = _usage_from_anthropic(payload)
    assert usage.cached_tokens == 80
    assert usage.cache_creation_tokens == 20


def test_usage_from_anthropic_handles_partial_cache_fields():
    payload = {
        "input_tokens": 100,
        "output_tokens": 30,
        "cache_read_input_tokens": 80,
    }
    usage = _usage_from_anthropic(payload)
    assert usage.cached_tokens == 80
    assert usage.cache_creation_tokens is None


def test_observation_payload_supported_true_when_any_cache_field_present():
    usage1 = UsageInfo(
        input_tokens=100, output_tokens=20, total_tokens=120, cached_tokens=64
    )
    p1 = build_prompt_cache_observation_payload(
        provider="openai", model="MiniMax-M2.7", usage=usage1
    )
    assert p1["supported"] is True
    assert p1["cached_tokens"] == 64
    assert "cache_creation_tokens" not in p1

    usage2 = UsageInfo(
        input_tokens=100,
        output_tokens=30,
        cached_tokens=80,
        cache_creation_tokens=20,
    )
    p2 = build_prompt_cache_observation_payload(
        provider="anthropic", model="claude-3", usage=usage2
    )
    assert p2["supported"] is True
    assert p2["cached_tokens"] == 80
    assert p2["cache_creation_tokens"] == 20


def test_usage_totals_from_mapping_extracts_cached_tokens_from_canonical_keys():
    t1 = usage_totals_from_mapping({"prompt_tokens": 50, "cached_tokens": 30})
    t2 = usage_totals_from_mapping({"prompt_tokens": 50, "cache_read_input_tokens": 30})
    t3 = usage_totals_from_mapping({"prompt_tokens": 50, "usage_cached_tokens": 30})
    for t in (t1, t2, t3):
        assert t is not None
        assert t.cached_tokens == 30


def test_footer_renders_cached_suffix_when_turn_cached_tokens_non_none():
    snapshot = TokenUsageSnapshot(
        turn_total_tokens=120,
        session_total_tokens=200,
        turn_cached_tokens=64,
    )
    out = format_token_usage_summary(snapshot)
    assert "120" in out
    assert "(64 cached)" in out


def test_footer_omits_cached_suffix_when_turn_cached_tokens_is_none():
    snapshot = TokenUsageSnapshot(
        turn_total_tokens=120,
        session_total_tokens=200,
        turn_cached_tokens=None,
    )
    out = format_token_usage_summary(snapshot)
    assert "120" in out
    assert "cached" not in out


def test_footer_includes_cached_suffix_only_in_turn_position_not_session():
    snapshot = TokenUsageSnapshot(
        turn_total_tokens=120,
        session_total_tokens=500,
        turn_cached_tokens=64,
        session_cached_tokens=200,
    )
    out = format_token_usage_summary(snapshot)
    assert out.index("(64 cached)") < out.index("session")


def test_build_snapshot_propagates_cached_tokens_from_totals():
    turn = TokenUsageTotals(
        prompt_tokens=80, completion_tokens=40, total_tokens=120, cached_tokens=64
    )
    session = TokenUsageTotals(
        prompt_tokens=200,
        completion_tokens=80,
        total_tokens=280,
        cached_tokens=150,
    )
    snap = build_token_usage_snapshot(
        turn=turn,
        session=session,
        context_used_tokens=None,
        context_limit_tokens=None,
        has_live_deltas=False,
        turn_elapsed_seconds=None,
        updated_at_monotonic=None,
    )
    assert snap.turn_cached_tokens == 64
    assert snap.session_cached_tokens == 150


def test_accumulate_usage_preserves_truthful_none_when_neither_side_reports():
    a = TokenUsageTotals(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    b = TokenUsageTotals(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    total = accumulate_usage(a, b)
    assert total is not None
    assert total.cached_tokens is None


def test_accumulate_usage_sums_cached_tokens_when_either_side_reports():
    a = TokenUsageTotals(
        prompt_tokens=10, completion_tokens=5, total_tokens=15, cached_tokens=4
    )
    b = TokenUsageTotals(
        prompt_tokens=20, completion_tokens=10, total_tokens=30, cached_tokens=8
    )
    total = accumulate_usage(a, b)
    assert total is not None
    assert total.cached_tokens == 12


def test_prompt_cache_observation_is_registered_in_event_catalog():
    from openminion.modules.telemetry.events.catalog import (
        EVENT_TYPES,
        PROMPT_CACHE_OBSERVATION,
    )

    assert PROMPT_CACHE_OBSERVATION == "prompt_cache_observation"
    assert PROMPT_CACHE_OBSERVATION in EVENT_TYPES
