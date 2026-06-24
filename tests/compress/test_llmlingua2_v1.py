from __future__ import annotations

from typing import Any, Dict, List
from unittest import mock

import pytest

from openminion.modules.context.compress.errors import MethodError
from openminion.modules.context.compress.methods.llmlingua2 import (
    LLMLingua2Compressor,
    _try_load_real_backend,
)
from openminion.modules.context.compress.schemas import CompressionPolicy, InputBlock


def _block(block_id: str, text: str) -> InputBlock:
    return InputBlock(
        block_id=block_id,
        type="retrieval",
        text=text,
        refs=[f"ref-{block_id}"],
        meta={"retrieval_score": 0.8, "source_id": block_id},
    )


_POLICY = CompressionPolicy(method_prepass=None)
_QUERY = "test query"


class _FakeBackend:
    def __init__(
        self,
        *,
        ratio: float = 0.5,
        track_calls: bool = False,
    ) -> None:
        self._ratio = ratio
        self._track_calls = track_calls
        self.calls: List[Dict[str, Any]] = []

    def compress_prompt(
        self,
        context: list[str],
        instruction: str,
        question: str,
        target_token: int,
    ) -> Dict[str, Any]:
        full_text = "\n".join(context)
        if not full_text.strip():
            return {"compressed_prompt": ""}
        cut = max(1, int(len(full_text) * self._ratio))
        compressed = full_text[:cut].rstrip()
        if self._track_calls:
            self.calls.append(
                {
                    "context": list(context),
                    "instruction": instruction,
                    "question": question,
                    "target_token": target_token,
                }
            )
        return {"compressed_prompt": compressed}


class TestLLMLingua2Compressor:
    def test_disabled_by_default(self):
        compressor = LLMLingua2Compressor()
        assert compressor.is_available() is False

    def test_unavailable_raises_method_error(self):
        compressor = LLMLingua2Compressor(available=False)
        with pytest.raises(MethodError):
            compressor.compress([_block("b1", "some text")], _POLICY, _QUERY)

    def test_available_with_injected_backend_compresses_successfully(self):
        compressor = LLMLingua2Compressor(available=True, backend=_FakeBackend())
        blocks = [
            _block("b1", "Jupiter is a gas giant in the outer solar system."),
            _block("b2", "Saturn is known for its prominent ring system."),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert len(list(result.blocks)) == 2
        assert result.method_id == "llmlingua2.v1"
        assert result.fallback_used is False

    def test_adapter_version_in_method_meta(self):
        compressor = LLMLingua2Compressor(
            available=True,
            adapter_version="v1.2-test",
            backend=_FakeBackend(),
        )
        blocks = [_block("b1", "some content here for the adapter")]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert result.method_meta["adapter_version"] == "v1.2-test"
        assert result.method_meta["method_id"] == "llmlingua2.v1"

    def test_empty_blocks_when_available(self):
        compressor = LLMLingua2Compressor(available=True, backend=_FakeBackend())
        result = compressor.compress([], _POLICY, _QUERY)
        assert list(result.blocks) == []

    def test_method_id_is_correct(self):
        compressor = LLMLingua2Compressor(available=True, backend=_FakeBackend())
        assert compressor.method_id == "llmlingua2.v1"


class TestLLM2RealBackendRoundTrip:
    def test_real_backend_round_trip(self):
        pytest.importorskip("llmlingua")
        compressor = LLMLingua2Compressor(available=True)
        # If the import succeeds but model load fails (no network / no
        # cache), the adapter returns is_available() == False. Skip in
        # that case rather than fail the suite.
        if not compressor.is_available():
            pytest.skip(
                "llmlingua is installed but the backend failed to "
                "instantiate (likely model-weight download required)."
            )
        blocks = [
            _block(
                "b1",
                "The quick brown fox jumps over the lazy dog repeatedly "
                "until the sun sets behind the distant blue mountains.",
            )
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        out = list(result.blocks)
        assert len(out) == 1
        assert out[0].text  # non-empty
        assert result.method_id == "llmlingua2.v1"


class TestLLM2FallbackWhenBackendUnavailable:
    def test_is_available_false_when_import_fails(self):
        # Use the public lazy-load helper to confirm the adapter
        # honors a failed import by returning False rather than raising.
        with mock.patch(
            "openminion.modules.context.compress.methods.llmlingua2._try_load_real_backend",
            return_value=None,
        ):
            compressor = LLMLingua2Compressor(available=True)
            assert compressor.is_available() is False

    def test_compress_raises_method_error_when_import_fails(self):
        with mock.patch(
            "openminion.modules.context.compress.methods.llmlingua2._try_load_real_backend",
            return_value=None,
        ):
            compressor = LLMLingua2Compressor(available=True)
            with pytest.raises(MethodError):
                compressor.compress([_block("b1", "text")], _POLICY, _QUERY)

    def test_try_load_returns_none_in_test_env(self):
        # In the test environment ``llmlingua`` is not installed, so the
        # public lazy-load helper must return None rather than raise.
        # This pins the production "soft-fail on missing extra" contract.
        try:
            import llmlingua  # noqa: F401
        except ImportError:
            assert _try_load_real_backend() is None
        else:
            pytest.skip("llmlingua is installed; this test only runs without it")


class TestLLM2TokenBudgetHonored:
    def test_target_ratio_scales_compression(self):
        long_text = ("Lorem ipsum dolor sit amet, " * 50).strip()
        blocks = [_block("b1", long_text)]
        tight_policy = CompressionPolicy(method_prepass=None, target_ratio=0.1)
        loose_policy = CompressionPolicy(method_prepass=None, target_ratio=0.9)

        backend_tight = _FakeBackend(ratio=0.5, track_calls=True)
        backend_loose = _FakeBackend(ratio=0.5, track_calls=True)
        c_tight = LLMLingua2Compressor(available=True, backend=backend_tight)
        c_loose = LLMLingua2Compressor(available=True, backend=backend_loose)

        c_tight.compress(blocks, tight_policy, _QUERY)
        c_loose.compress(blocks, loose_policy, _QUERY)

        tight_budget = backend_tight.calls[0]["target_token"]
        loose_budget = backend_loose.calls[0]["target_token"]
        # Loose ratio should pass a larger budget to the backend than tight.
        assert loose_budget > tight_budget, (
            f"expected loose budget ({loose_budget}) > tight budget "
            f"({tight_budget}) when ratio 0.9 vs 0.1"
        )


class TestLLM2DeterministicOutput:
    def test_deterministic_output(self):
        blocks = [
            _block("b1", "Earth orbits the Sun once a year."),
            _block("b2", "The Moon orbits Earth roughly once a month."),
        ]
        compressor_a = LLMLingua2Compressor(available=True, backend=_FakeBackend())
        compressor_b = LLMLingua2Compressor(available=True, backend=_FakeBackend())
        result_a = compressor_a.compress(blocks, _POLICY, _QUERY)
        result_b = compressor_b.compress(blocks, _POLICY, _QUERY)
        texts_a = [b.text for b in result_a.blocks]
        texts_b = [b.text for b in result_b.blocks]
        assert texts_a == texts_b
