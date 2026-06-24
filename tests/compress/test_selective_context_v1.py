from __future__ import annotations

from openminion.modules.context.compress.methods.selective_context import (
    SelectiveContextPrepass,
)
from openminion.modules.context.compress.schemas import InputBlock


def _block(block_id: str, text: str, refs=None) -> InputBlock:
    return InputBlock(
        block_id=block_id,
        type="retrieval",
        text=text,
        refs=refs or [f"ref-{block_id}"],
        meta={},
    )


class TestSelectiveContextPrepass:
    def test_is_available(self):
        assert SelectiveContextPrepass().is_available() is True

    def test_no_duplicates_retains_all(self):
        prepass = SelectiveContextPrepass()
        blocks = [
            _block("b1", "Jupiter is a gas giant in the outer solar system."),
            _block("b2", "Saturn has a prominent ring system made of ice and rock."),
            _block("b3", "Mars is a rocky planet with a thin atmosphere."),
        ]
        result = prepass.compress(blocks)
        assert len(result.blocks) == 3
        assert result.dropped_reason_stats.get("duplicate", 0) == 0

    def test_exact_duplicate_is_dropped(self):
        prepass = SelectiveContextPrepass(threshold=0.7)
        text = "Jupiter is a very large gas giant planet."
        blocks = [
            _block("b1", text),
            _block("b2", text),  # exact duplicate
            _block("b3", "Mars is a different planet."),
        ]
        result = prepass.compress(blocks)
        # b2 is dropped as near-duplicate of b1
        assert len(result.blocks) == 2
        assert result.dropped_reason_stats["duplicate"] == 1

    def test_near_duplicate_is_dropped(self):
        prepass = SelectiveContextPrepass(threshold=0.7)
        blocks = [
            _block("b1", "The quick brown fox jumped over the lazy dog"),
            _block(
                "b2", "The quick brown fox jumped over the lazy dog today"
            ),  # near-dup
        ]
        result = prepass.compress(blocks)
        assert len(result.blocks) == 1
        assert result.dropped_reason_stats.get("duplicate", 0) == 1

    def test_different_texts_retained(self):
        prepass = SelectiveContextPrepass(threshold=0.9)
        blocks = [
            _block("b1", "cats are small furry animals"),
            _block("b2", "dogs are loyal companions to humans"),
        ]
        result = prepass.compress(blocks)
        assert len(result.blocks) == 2
        assert result.dropped_reason_stats.get("duplicate", 0) == 0

    def test_output_is_deterministic(self):
        prepass = SelectiveContextPrepass()
        blocks = [
            _block("b1", "a b c d"),
            _block("b2", "e f g h"),
            _block("b3", "a b c d"),  # dup of b1
        ]
        result_a = prepass.compress(blocks)
        result_b = prepass.compress(blocks)
        assert [b.block_id for b in result_a.blocks] == [
            b.block_id for b in result_b.blocks
        ]
        assert result_a.dropped_reason_stats == result_b.dropped_reason_stats

    def test_dropped_reason_codes_are_correct(self):
        prepass = SelectiveContextPrepass(threshold=0.5)
        blocks = [
            _block("b1", "hello world foo bar"),
            _block("b2", "hello world foo baz"),  # high overlap
        ]
        result = prepass.compress(blocks)
        if result.dropped_reason_stats:
            reasons = set(result.dropped_reason_stats.keys())
            assert reasons <= {"duplicate"}

    def test_empty_blocks_input(self):
        prepass = SelectiveContextPrepass()
        result = prepass.compress([])
        assert list(result.blocks) == []
        assert result.dropped_reason_stats == {}
