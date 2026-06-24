from __future__ import annotations

from openminion.modules.context.compress.methods.recomp_extractive import (
    RecompExtractiveCompressor,
)
from openminion.modules.context.compress.schemas import CompressionPolicy, InputBlock


def _block(
    block_id: str,
    text: str,
    retrieval_score: float = 0.5,
    source_id: str | None = None,
) -> InputBlock:
    return InputBlock(
        block_id=block_id,
        type="retrieval",
        text=text,
        refs=[f"ref-{block_id}"],
        meta={
            "retrieval_score": retrieval_score,
            "source_id": source_id or block_id,
        },
    )


_POLICY = CompressionPolicy(method_prepass=None, allow_empty_augmentation=True)
_QUERY = "What are planets?"


class TestRecompExtractiveCompressor:
    def test_is_available(self):
        assert RecompExtractiveCompressor().is_available() is True

    def test_top_n_per_source_is_honored(self):
        compressor = RecompExtractiveCompressor(top_n=1)
        blocks = [
            _block("b1", "Jupiter is large.", 0.9, source_id="src-1"),
            _block("b2", "Jupiter also has moons.", 0.8, source_id="src-1"),
            _block("b3", "Saturn has rings.", 0.7, source_id="src-2"),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert result.empty_augmentation is False
        # Only 1 per source: src-1 gives 1 block, src-2 gives 1 block.
        assert len(result.blocks) <= 2

    def test_low_quality_triggers_empty_augmentation(self):
        compressor = RecompExtractiveCompressor(quality_floor=0.9)
        blocks = [
            _block("b1", "low score content", 0.1),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert result.empty_augmentation is True
        assert result.empty_reason == "low_quality"
        assert list(result.blocks) == []

    def test_empty_augmentation_is_deterministic(self):
        compressor = RecompExtractiveCompressor(quality_floor=0.9)
        blocks = [_block("b1", "weak content", 0.05)]
        result_a = compressor.compress(blocks, _POLICY, _QUERY)
        result_b = compressor.compress(blocks, _POLICY, _QUERY)
        assert result_a.empty_augmentation == result_b.empty_augmentation
        assert result_a.empty_reason == result_b.empty_reason

    def test_no_empty_augmentation_when_disabled(self):
        policy = CompressionPolicy(method_prepass=None, allow_empty_augmentation=False)
        compressor = RecompExtractiveCompressor(quality_floor=0.9)
        blocks = [_block("b1", "weak content", 0.05)]
        result = compressor.compress(blocks, policy, _QUERY)
        # With allow_empty_augmentation=False, should return what it has (possibly empty list
        # without augmentation flag).
        assert result.empty_augmentation is False

    def test_normal_case_produces_blocks(self):
        compressor = RecompExtractiveCompressor(top_n=2, quality_floor=0.0)
        blocks = [
            _block("b1", "Jupiter is massive.", 0.9, source_id="s1"),
            _block("b2", "Saturn has rings.", 0.8, source_id="s2"),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        assert len(result.blocks) > 0
        assert result.empty_augmentation is False

    def test_dropped_reason_stats_source_cap(self):
        compressor = RecompExtractiveCompressor(top_n=1, quality_floor=0.0)
        blocks = [
            _block("b1", "Jupiter content A.", 0.9, source_id="same"),
            _block("b2", "Jupiter content B.", 0.8, source_id="same"),
            _block("b3", "Jupiter content C.", 0.7, source_id="same"),
        ]
        result = compressor.compress(blocks, _POLICY, _QUERY)
        # 2 should be dropped due to source_cap (top_n=1 per source)
        assert result.dropped_reason_stats.get("source_cap", 0) == 2

    def test_output_is_deterministic_across_runs(self):
        compressor = RecompExtractiveCompressor(top_n=2, quality_floor=0.0)
        blocks = [
            _block(f"b{i}", f"Content about block {i}.", 0.5 + i * 0.1)
            for i in range(5)
        ]
        r1 = compressor.compress(blocks, _POLICY, _QUERY)
        r2 = compressor.compress(blocks, _POLICY, _QUERY)
        assert [b.block_id for b in r1.blocks] == [b.block_id for b in r2.blocks]
