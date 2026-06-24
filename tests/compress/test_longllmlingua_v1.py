from __future__ import annotations

import pytest

from openminion.modules.context.compress.methods.longllmlingua import (
    LongLLMLinguaCompressor,
)
from openminion.modules.context.compress.errors import MethodError
from openminion.modules.context.compress.schemas import CompressionPolicy, InputBlock


def _block(block_id: str, text: str, retrieval_score: float = 0.5) -> InputBlock:
    return InputBlock(
        block_id=block_id,
        type="retrieval",
        text=text,
        refs=[f"ref-{block_id}"],
        meta={"retrieval_score": retrieval_score, "source_id": block_id},
    )


_POLICY = CompressionPolicy(method_prepass=None)
_QUERY = "What are gas giants?"


class TestLongLLMLinguaCompressor:
    def test_unavailable_raises_method_error(self):
        compressor = LongLLMLinguaCompressor(available=False)
        assert compressor.is_available() is False
        with pytest.raises(MethodError):
            compressor.compress([], _POLICY, _QUERY)

    def test_available_compresses_successfully(self):
        compressor = LongLLMLinguaCompressor(available=True)
        blocks = [
            _block("b1", "Jupiter is a gas giant.", 0.9),
            _block("b2", "Saturn has rings.", 0.8),
            _block("b3", "Mars is a rocky planet.", 0.2),
            _block("b4", "Neptune is an ice giant.", 0.7),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert len(result.blocks) > 0
        assert result.method_id == "longllmlingua.v1"

    def test_fallback_determinism(self):
        compressor = LongLLMLinguaCompressor(available=True, segment_size=2)
        blocks = [
            _block(f"b{i}", f"Block {i} text content.", 0.5 + i * 0.05)
            for i in range(6)
        ]
        result_a = compressor.compress(blocks, _POLICY, _QUERY)
        result_b = compressor.compress(blocks, _POLICY, _QUERY)
        block_ids_a = [b.block_id for b in result_a.blocks]
        block_ids_b = [b.block_id for b in result_b.blocks]
        assert block_ids_a == block_ids_b

    def test_high_score_segments_are_preferred(self):
        compressor = LongLLMLinguaCompressor(available=True, segment_size=1)
        blocks = [
            _block("low1", "low-scored content", 0.1),
            _block("low2", "more low content", 0.1),
            _block("high1", "high-scored content", 0.95),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        retained_ids = {b.block_id for b in result.blocks}
        assert "high1" in retained_ids

    def test_empty_blocks_returns_empty(self):
        compressor = LongLLMLinguaCompressor(available=True)
        result = compressor.compress([], _POLICY, _QUERY)
        assert list(result.blocks) == []

    def test_method_meta_is_present(self):
        compressor = LongLLMLinguaCompressor(available=True)
        blocks = [_block("b1", "some content", 0.8)]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert "method_id" in result.method_meta
        assert result.method_meta["method_id"] == "longllmlingua.v1"
