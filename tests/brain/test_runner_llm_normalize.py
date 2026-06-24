from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.llm.providers.normalization import normalize_provider_response


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
