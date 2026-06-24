from __future__ import annotations

import pytest

from openminion.modules.context.compress.service import CompressionService
from openminion.modules.context.compress.errors import ValidationError
from openminion.modules.context.compress.schemas import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionRequest,
    InputBlock,
)


def _make_request(
    request_id: str = "req-1",
    blocks: list | None = None,
    quality_hint=None,
    policy: CompressionPolicy | None = None,
) -> CompressionRequest:
    if blocks is None:
        blocks = [
            InputBlock(
                block_id="b1",
                type="retrieval",
                text="Jupiter is a gas giant.",
                refs=["doc#1"],
                meta={"retrieval_score": 0.9, "source_id": "s1"},
            ),
            InputBlock(
                block_id="b2",
                type="retrieval",
                text="Saturn has prominent rings.",
                refs=["doc#2"],
                meta={"retrieval_score": 0.7, "source_id": "s2"},
            ),
        ]
    return CompressionRequest(
        request_id=request_id,
        query="What are gas giants?",
        blocks=blocks,
        budgets=CompressionBudgets(
            max_output_tokens_total=512,
            reserve_tokens_for_headers=16,
            hard_cap=True,
        ),
        policy=policy or CompressionPolicy(method_prepass=None),
        engine_version="2026-03-01",
        retrieval_quality_hint=quality_hint,
    )


class TestCompressionService:
    def test_compress_returns_result_and_run_id(self):
        svc = CompressionService()
        request = _make_request()
        result, run_id = svc.compress(request)

        assert result is not None
        assert isinstance(run_id, str)
        assert len(result.blocks) > 0
        assert result.method_id == "extractive.v1"
        assert result.input_tokens > 0
        assert result.output_tokens > 0
        assert 0.0 <= result.ratio <= 1.0
        assert result.compression_hash != ""

    def test_compress_is_deterministic(self):
        svc = CompressionService()
        request = _make_request()
        result_a, _ = svc.compress(request)
        result_b, _ = svc.compress(request)

        assert result_a.compression_hash == result_b.compression_hash
        assert result_a.ratio == result_b.ratio
        assert len(result_a.blocks) == len(result_b.blocks)

    def test_explain_returns_deterministic_payload(self):
        svc = CompressionService()
        request = _make_request()
        result, run_id = svc.compress(request)

        payload = svc.explain(run_id)
        assert payload is not None
        assert payload.run_id == run_id
        assert payload.method_id == result.method_id
        assert payload.compression_hash == result.compression_hash
        assert payload.input_tokens == result.input_tokens
        assert payload.output_tokens == result.output_tokens

    def test_explain_returns_none_for_missing_run(self):
        svc = CompressionService()
        assert svc.explain("nonexistent-run") is None

    def test_explain_payload_has_dropped_reasons(self):
        svc = CompressionService()
        # Use many blocks from same source to trigger source_cap drops
        blocks = [
            InputBlock(
                block_id=f"b{i}",
                type="retrieval",
                text=f"Block {i} content.",
                refs=[f"doc#{i}"],
                meta={"retrieval_score": 0.5, "source_id": "same-source"},
            )
            for i in range(5)
        ]
        request = _make_request(request_id="req-drops", blocks=blocks)
        result, run_id = svc.compress(request)
        payload = svc.explain(run_id)

        assert payload is not None
        assert isinstance(payload.dropped_reason_stats, dict)

    def test_compress_validation_rejects_empty_query(self):
        svc = CompressionService()
        with pytest.raises(ValidationError):
            svc.compress(
                CompressionRequest(
                    request_id="req-bad",
                    query="",
                    blocks=[
                        InputBlock(
                            block_id="b1",
                            type="retrieval",
                            text="text",
                            refs=[],
                            meta={},
                        )
                    ],
                    budgets=CompressionBudgets(max_output_tokens_total=512),
                    policy=CompressionPolicy(method_prepass=None),
                    engine_version="2026-03-01",
                )
            )

    def test_compress_validation_rejects_empty_blocks(self):
        svc = CompressionService()
        with pytest.raises(ValidationError):
            svc.compress(
                CompressionRequest(
                    request_id="req-bad",
                    query="What?",
                    blocks=[],
                    budgets=CompressionBudgets(max_output_tokens_total=512),
                    policy=CompressionPolicy(method_prepass=None),
                    engine_version="2026-03-01",
                )
            )

    def test_compress_report_fields_are_complete(self):
        svc = CompressionService()
        request = _make_request()
        result, _ = svc.compress(request)

        report = result.report
        assert report.policy_hash != ""
        assert report.input_hash != ""
        assert report.output_hash != ""
        assert report.tokenizer_id == "whitespace.v1"
        assert report.scorer_version == "v1.0"
        assert isinstance(report.dropped_reason_stats, dict)
        assert isinstance(report.count_by_type, dict)

    def test_compress_mode_name_biases_retained_block_count(self):
        svc = CompressionService()
        blocks = [
            InputBlock(
                block_id=f"b{i}",
                type="retrieval",
                text=f"Fact block {i}.",
                refs=[f"doc#{i}"],
                meta={"retrieval_score": 1.0 - (i * 0.05), "source_id": f"s{i}"},
            )
            for i in range(4)
        ]
        policy = CompressionPolicy(method_prepass=None)
        budgets = CompressionBudgets(
            max_output_tokens_total=7,
            reserve_tokens_for_headers=0,
            hard_cap=True,
        )

        respond_result, _ = svc.compress(
            CompressionRequest(
                request_id="req-respond",
                query="Summarize briefly",
                blocks=blocks,
                budgets=budgets,
                policy=policy,
                engine_version="2026-03-01",
                mode_name="respond",
            )
        )
        plan_result, _ = svc.compress(
            CompressionRequest(
                request_id="req-plan",
                query="Preserve planning constraints",
                blocks=blocks,
                budgets=budgets,
                policy=policy,
                engine_version="2026-03-01",
                mode_name="plan",
            )
        )

        assert len(plan_result.blocks) >= len(respond_result.blocks)
