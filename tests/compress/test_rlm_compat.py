from __future__ import annotations

from openminion.modules.context.compress.service import CompressionService
from openminion.modules.context.compress.schemas import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionRequest,
    CompressionResult,
    InputBlock,
)


def _make_request(request_id: str = "req-rlm") -> CompressionRequest:
    return CompressionRequest(
        request_id=request_id,
        query="What is the retrieval quality?",
        blocks=[
            InputBlock(
                block_id="b1",
                type="retrieval",
                text="High-confidence evidence block.",
                refs=["doc#1"],
                meta={"retrieval_score": 0.9, "source_id": "s1"},
            ),
            InputBlock(
                block_id="b2",
                type="dialogue",
                text="User asked about retrieval quality.",
                refs=["conv#1"],
                meta={"retrieval_score": 0.6, "source_id": "s2"},
            ),
        ],
        budgets=CompressionBudgets(
            max_output_tokens_total=512,
            hard_cap=True,
        ),
        policy=CompressionPolicy(method_prepass=None),
        engine_version="2026-03-01",
    )


class TestRlmCompatibility:
    def _compress(self, request: CompressionRequest) -> CompressionResult:
        svc = CompressionService()
        result, _ = svc.compress(request)
        return result

    def test_result_has_blocks(self):
        result = self._compress(_make_request())
        assert hasattr(result, "blocks")
        assert isinstance(result.blocks, list)

    def test_result_has_method_id(self):
        result = self._compress(_make_request())
        assert result.method_id == "extractive.v1"

    def test_result_has_ratio(self):
        result = self._compress(_make_request())
        assert isinstance(result.ratio, float)
        assert 0.0 <= result.ratio

    def test_result_has_input_tokens(self):
        result = self._compress(_make_request())
        assert isinstance(result.input_tokens, int)
        assert result.input_tokens > 0

    def test_result_has_output_tokens(self):
        result = self._compress(_make_request())
        assert isinstance(result.output_tokens, int)
        assert result.output_tokens >= 0

    def test_result_has_compression_hash(self):
        result = self._compress(_make_request())
        assert isinstance(result.compression_hash, str)
        assert len(result.compression_hash) == 64  # sha256 hex

    def test_result_report_has_empty_augmentation(self):
        result = self._compress(_make_request())
        assert hasattr(result.report, "empty_augmentation")
        assert isinstance(result.report.empty_augmentation, bool)

    def test_method_tier_bad_uses_baseline(self):
        request = CompressionRequest(
            request_id="req-bad",
            query="test",
            blocks=[
                InputBlock(
                    block_id="b1",
                    type="retrieval",
                    text="some evidence",
                    refs=["ref#1"],
                    meta={"retrieval_score": 0.3, "source_id": "s1"},
                )
            ],
            budgets=CompressionBudgets(max_output_tokens_total=512),
            policy=CompressionPolicy(method_prepass=None),
            engine_version="2026-03-01",
            retrieval_quality_hint="BAD",
        )
        result = self._compress(request)
        assert result.method_id == "extractive.v1"

    def test_result_to_dict_includes_all_rlm_fields(self):
        result = self._compress(_make_request())
        d = result.to_dict()
        required_keys = {
            "blocks",
            "method_id",
            "ratio",
            "input_tokens",
            "output_tokens",
            "compression_hash",
            "report",
        }
        assert required_keys <= set(d.keys())
        assert "empty_augmentation" in d["report"]
