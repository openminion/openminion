from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.entry_routing import _response_usage_payload
from openminion.modules.llm.providers.normalization import normalize_provider_response
from openminion.modules.llm.schemas import UsageInfo


def test_brain_llm_normalization_basic_shape() -> None:
    raw = SimpleNamespace(
        output_text="ok",
        model="",
        usage=SimpleNamespace(input_tokens=2, output_tokens=3, total_tokens=None),
        tool_calls=[],
        finish_reason="stop",
        normalization={},
    )

    normalized = normalize_provider_response(raw, provider_name="openrouter")

    assert normalized.text == "ok"
    assert normalized.model == "openrouter"
    assert normalized.usage["prompt_tokens"] == 2
    assert normalized.usage["completion_tokens"] == 3
    assert normalized.finish_reason == "stop"


def test_entry_usage_payload_preserves_provenance_and_cache_dimensions() -> None:
    payload = _response_usage_payload(
        SimpleNamespace(
            usage=UsageInfo(
                input_tokens=8,
                output_tokens=2,
                total_tokens=10,
                total_source="derived",
                cached_tokens=4,
                cache_creation_tokens=1,
            )
        )
    )

    assert payload == {
        "input_tokens": 8,
        "output_tokens": 2,
        "total_tokens": 10,
        "total_source": "derived",
        "cached_tokens": 4,
        "cache_creation_tokens": 1,
    }
