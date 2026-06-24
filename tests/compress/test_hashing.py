from dataclasses import replace
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from openminion.modules.context.compress.hashing import (  # noqa: E402
    build_canonical_payload,
    canonical_hash,
    compute_compression_hash,
    diff_payloads,
)
from openminion.modules.context.compress.schemas import (  # noqa: E402
    CompressionBudgets,
    CompressionPolicy,
    CompressionReport,
    CompressionRequest,
    CompressionResult,
    CompressedBlock,
    InputBlock,
)


def _sample_request() -> CompressionRequest:
    policy = CompressionPolicy(method_prepass=None)
    budgets = CompressionBudgets(
        max_output_tokens_total=512,
        max_output_tokens_by_type={"retrieval": 256},
        reserve_tokens_for_headers=32,
        hard_cap=True,
    )
    blocks = [
        InputBlock(
            block_id="b1",
            type="retrieval",
            text="alpha",
            refs=["doc#1"],
            meta={"source_id": "s1", "retrieval_score": 0.9},
        )
    ]
    return CompressionRequest(
        request_id="req-1",
        query="What happened?",
        blocks=blocks,
        budgets=budgets,
        policy=policy,
        engine_version="2026-03-01",
    )


def _report() -> CompressionReport:
    return CompressionReport(
        empty_augmentation=False,
        empty_reason=None,
        dropped_reason_stats={"budget": 1, "irrelevant": 2},
        count_by_type={"retrieval": 1},
        fallback_used=False,
        policy_hash="policy@1",
        input_hash="input@1",
        output_hash="output@1",
        engine_version="engine@1",
        tokenizer_id="tokenizer@1",
        scorer_version="scorer@1",
    )


BLOCK_REFS = {
    "b1": ["ref-a", "ref-b"],
    "b2": ["ref-c", "ref-d"],
}


def _block(block_id: str) -> CompressedBlock:
    return CompressedBlock(
        block_id=block_id,
        type="retrieval",
        text=f"text-{block_id}",
        refs=list(BLOCK_REFS[block_id]),
        unit_refs=[f"{block_id}:unit", f"{block_id}:unit-extra"],
        compression_meta={"method_id": "extractive.v1", "input_tokens": 100},
    )


def _result(block_order=("b1", "b2")) -> CompressionResult:
    blocks = [
        _block(block_order[0]),
        _block(block_order[1]),
    ]
    report = _report()
    return CompressionResult(
        blocks=blocks,
        report=report,
        method_id="extractive.v1",
        input_tokens=200,
        output_tokens=100,
        ratio=0.5,
        compression_hash="",
        warnings=["low_score", "budget"],
    )


def test_compute_compression_hash_deterministic_across_order():
    request = _sample_request()
    result_a = _result()
    # reorder blocks + warnings to ensure canonicalization
    result_b = _result(block_order=("b2", "b1"))
    result_b = replace(result_b, warnings=list(reversed(result_b.warnings)))

    payload_a = build_canonical_payload(request, result_a)
    payload_b = build_canonical_payload(request, result_b)
    assert payload_a == payload_b

    hash_a = compute_compression_hash(request, result_a)
    hash_b = compute_compression_hash(request, result_b)
    assert hash_a == hash_b


def test_canonical_hash_stable_against_json_key_order():
    payload = {"b": [2, 1], "a": {"y": 2, "x": 1}}
    reordered = {"a": {"x": 1, "y": 2}, "b": [2, 1]}
    assert canonical_hash(payload) == canonical_hash(reordered)


def test_diff_payloads_reports_path_differences():
    payload_a = {"foo": {"bar": [1, 2, 3]}}
    payload_b = {"foo": {"bar": [1, 4], "baz": 1}}
    diffs = diff_payloads(payload_a, payload_b)
    assert "foo.bar[1]" in diffs
    assert "foo.bar[2]" in diffs
    assert "foo.baz" in diffs


def test_build_canonical_payload_rounds_ratio():
    request = _sample_request()
    result = replace(_result(), ratio=0.333333333)
    payload = build_canonical_payload(request, result)
    assert payload["result"]["ratio"] == 0.333333
    # warnings sorted
    assert payload["result"]["warnings"] == sorted(result.warnings)
