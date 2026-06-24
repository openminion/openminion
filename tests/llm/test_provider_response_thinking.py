from __future__ import annotations

from openminion.modules.llm.contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from openminion.modules.llm.providers.contracts import (
    ProviderResponse,
    ThinkingBlock,
)
from openminion.modules.llm.providers.normalization import (
    _coerce_thinking_blocks,
    normalize_provider_response,
)


class TestProviderResponseThinkingField:
    def test_default_is_empty_list(self):
        r = ProviderResponse(text="hi", model="m")
        assert r.thinking == []

    def test_accepts_typed_blocks(self):
        block = ThinkingBlock(content="hmm")
        r = ProviderResponse(text="hi", model="m", thinking=[block])
        assert r.thinking == [block]

    def test_thinking_block_has_type_discriminator(self):
        block = ThinkingBlock(content="reasoning text")
        assert block.type == "thinking"
        assert block.signature is None
        assert block.redacted is False


class TestCoerceThinkingBlocks:
    def test_none_returns_empty(self):
        assert _coerce_thinking_blocks(None) == []

    def test_non_list_returns_empty(self):
        assert _coerce_thinking_blocks("oops") == []
        assert _coerce_thinking_blocks(42) == []
        assert _coerce_thinking_blocks({"foo": "bar"}) == []

    def test_typed_blocks_pass_through(self):
        block = ThinkingBlock(content="x")
        result = _coerce_thinking_blocks([block])
        assert result == [block]
        assert isinstance(result[0], ThinkingBlock)

    def test_dicts_are_coerced(self):
        result = _coerce_thinking_blocks(
            [
                {"content": "thought 1"},
                {"content": "thought 2", "signature": "sig", "redacted": True},
            ]
        )
        assert len(result) == 2
        assert all(isinstance(b, ThinkingBlock) for b in result)
        assert result[0].content == "thought 1"
        assert result[1].signature == "sig"
        assert result[1].redacted is True

    def test_malformed_entries_are_skipped(self):
        # Mix of valid and broken — the helper must not raise.
        result = _coerce_thinking_blocks(
            [
                {"content": "good"},
                42,  # not a dict or ThinkingBlock
                None,
                "stringy",
            ]
        )
        # Only the first entry survives coercion.
        assert len(result) == 1
        assert result[0].content == "good"


class TestNormalizeForwardsThinking:
    def test_thinking_survives_normalization(self):
        block = ThinkingBlock(content="reasoning here")
        raw = ProviderResponse(text="hello", model="m1", thinking=[block])
        normalized = normalize_provider_response(raw, provider_name="anthropic")
        assert len(normalized.thinking) == 1
        assert normalized.thinking[0].content == "reasoning here"

    def test_dict_response_with_thinking_is_coerced(self):
        raw_dict = {
            "text": "hi",
            "model": "m1",
            "thinking": [{"content": "from dict"}],
        }
        normalized = normalize_provider_response(raw_dict, provider_name="openrouter")
        assert len(normalized.thinking) == 1
        assert isinstance(normalized.thinking[0], ThinkingBlock)
        assert normalized.thinking[0].content == "from dict"

    def test_response_without_thinking_normalizes_to_empty_list(self):
        raw = ProviderResponse(text="hi", model="m1")
        normalized = normalize_provider_response(raw, provider_name="anthropic")
        assert normalized.thinking == []

    def test_adapter_result_to_llm_response_preserves_thinking(self):
        llm_response = adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider="openai",
                model="o3",
                output_text="",
                thinking=[{"type": "thinking", "content": "use the tool first"}],
            )
        )
        assert len(llm_response.thinking) == 1
        assert llm_response.thinking[0]["content"] == "use the tool first"


class TestThinkingPassthroughFlag:
    def test_passthrough_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("OPENMINION_PROVIDER_THINKING_PASSTHROUGH", raising=False)
        raw = ProviderResponse(
            text="hi",
            model="m1",
            thinking=[ThinkingBlock(content="reasoning")],
        )
        normalized = normalize_provider_response(raw, provider_name="anthropic")
        assert len(normalized.thinking) == 1

    def test_passthrough_disabled_strips_blocks(self, monkeypatch):
        monkeypatch.setenv("OPENMINION_PROVIDER_THINKING_PASSTHROUGH", "false")
        raw = ProviderResponse(
            text="hi",
            model="m1",
            thinking=[ThinkingBlock(content="reasoning")],
        )
        normalized = normalize_provider_response(raw, provider_name="anthropic")
        assert normalized.thinking == []

    def test_passthrough_truthy_values_accepted(self, monkeypatch):
        # Accept multiple truthy/falsy spellings consistent with the
        # shared env-helper's get_bool semantics.
        for value in ("1", "true", "yes", "TRUE"):
            monkeypatch.setenv("OPENMINION_PROVIDER_THINKING_PASSTHROUGH", value)
            raw = ProviderResponse(
                text="hi",
                model="m1",
                thinking=[ThinkingBlock(content="reasoning")],
            )
            normalized = normalize_provider_response(raw, provider_name="anthropic")
            assert len(normalized.thinking) == 1, (
                f"value={value!r} should enable passthrough"
            )
