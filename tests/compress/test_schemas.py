from openminion.modules.context.compress.schemas import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionReport,
    CompressionRequest,
    CompressionResult,
    CompressedBlock,
    InputBlock,
)


def _sample_blocks():
    return [
        InputBlock(
            block_id="b1",
            type="retrieval",
            text="Important fact",
            refs=["doc1#L10"],
            meta={"retrieval_score": 0.9, "trust_tier": "gold"},
        ),
        InputBlock(
            block_id="b2",
            type="dialogue",
            text="User said hi",
            refs=["conv#1"],
            meta={"recency_ts": 123456789},
        ),
    ]


def _sample_budgets():
    return CompressionBudgets(
        max_output_tokens_total=1024,
        max_output_tokens_by_type={"retrieval": 512},
        reserve_tokens_for_headers=64,
        hard_cap=True,
    )


def _sample_policy():
    return CompressionPolicy(
        mode="extractive",
        target_ratio=0.3,
        min_evidence_items=2,
        max_items_per_source=3,
        allow_empty_augmentation=True,
        faithfulness_level="strict",
        quote_budget_tokens=256,
        preserve_refs=True,
        positioning="frontload_key_evidence",
        method_prepass="selective_context",
        method_main="extractive.v1",
        fallback_method_id="extractive.v1",
        abstractive_enabled=False,
    )


def test_compression_request_roundtrip():
    request = CompressionRequest(
        request_id="req-1",
        query="What happened?",
        blocks=_sample_blocks(),
        budgets=_sample_budgets(),
        policy=_sample_policy(),
        engine_version="2026-03-01",
        trace_id="trace-123",
        session_id="sess-abc",
        retrieval_quality_hint="GOOD",
    )

    assert request.blocks[0].meta["retrieval_score"] == 0.9
    assert request.budgets.max_output_tokens_total == 1024
    assert request.policy.method_main == "extractive.v1"


def test_compression_result_to_dict_structure():
    report = CompressionReport(
        empty_augmentation=False,
        empty_reason=None,
        dropped_reason_stats={"irrelevant": 1},
        count_by_type={"retrieval": 1},
        fallback_used=False,
        policy_hash="hash-policy",
        input_hash="hash-in",
        output_hash="hash-out",
        engine_version="2026-03-01",
        tokenizer_id="tiktoken@latest",
        scorer_version="scorer@0",
    )

    compressed_block = CompressedBlock(
        block_id="b1",
        type="retrieval",
        text="Compressed text",
        refs=["doc1#L10"],
        unit_refs=["u1"],
        compression_meta={
            "method_id": "extractive.v1",
            "input_tokens": 200,
            "output_tokens": 100,
            "ratio": 0.5,
            "content_hash_in": "in",
            "content_hash_out": "out",
        },
    )

    result = CompressionResult(
        blocks=[compressed_block],
        report=report,
        method_id="extractive.v1",
        input_tokens=200,
        output_tokens=100,
        ratio=0.5,
        compression_hash="hash-comp",
        warnings=["budget_warning"],
    )

    result_dict = result.to_dict()
    assert result_dict["blocks"][0]["compression_meta"]["method_id"] == "extractive.v1"
    assert result_dict["report"]["policy_hash"] == "hash-policy"
    assert result_dict["warnings"] == ["budget_warning"]


def test_policy_defaults_match_spec():
    policy = CompressionPolicy()
    assert policy.mode == "extractive"
    assert policy.target_ratio == 0.25
    assert policy.allow_empty_augmentation is True
    assert policy.preserve_refs is True
    assert policy.method_prepass == "selective_context"
    assert policy.fallback_method_id == "extractive.v1"
    assert policy.abstractive_enabled is False
