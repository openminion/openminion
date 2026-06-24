from openminion.modules.context.compress.methods.extractive import ExtractiveCompressor
from openminion.modules.context.compress.schemas import CompressionPolicy, InputBlock


def _block(block_id: str, **meta) -> InputBlock:
    refs = meta.pop("refs", [f"doc#{block_id}"])
    block_type = meta.pop("type", "retrieval")
    text = meta.pop("text", f"text for {block_id}")
    block_meta = {**meta}
    return InputBlock(
        block_id=block_id,
        type=block_type,
        text=text,
        refs=refs,
        meta=block_meta,
    )


def test_extractive_selection_orders_by_score_and_tie_breaks():
    policy = CompressionPolicy(max_items_per_source=2)
    compressor = ExtractiveCompressor(max_units=2)
    blocks = [
        _block(
            "b1",
            retrieval_score=0.9,
            trust_tier="gold",
            source_index=1,
            text="alpha beta",
        ),
        _block(
            "b2",
            retrieval_score=0.8,
            trust_tier="silver",
            source_index=0,
            text="beta gamma",
        ),
        _block(
            "b3", retrieval_score=0.5, trust_tier="gold", source_index=2, text="delta"
        ),
    ]

    result = compressor.compress(blocks, policy, query="beta question")

    assert len(result.blocks) == 2
    block_ids = [block.block_id for block in result.blocks]
    assert block_ids == ["b1", "b2"]
    assert result.blocks[0].text == "alpha beta"
    assert result.blocks[1].text == "beta gamma"
    assert all(block.refs for block in result.blocks)
    assert result.dropped_reason_stats.get("budget", 0) >= 1


def test_extractive_respects_max_items_per_source():
    policy = CompressionPolicy(max_items_per_source=1)
    compressor = ExtractiveCompressor(max_units=3)
    blocks = [
        _block("s1-a", source_id="src1", retrieval_score=0.9),
        _block("s1-b", source_id="src1", retrieval_score=0.8),
        _block("s2-a", source_id="src2", retrieval_score=0.7),
    ]

    result = compressor.compress(blocks, policy, query="fact")

    selected_ids = [block.block_id for block in result.blocks]
    assert selected_ids == ["s1-a", "s2-a"]
    assert all(block.refs for block in result.blocks)
    assert result.dropped_reason_stats.get("source_cap", 0) == 1
