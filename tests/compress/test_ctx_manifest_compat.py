from __future__ import annotations

from openminion.modules.context.compress.service import CompressionService
from openminion.modules.context.compress.schemas import (
    CompressionBudgets,
    CompressionPolicy,
    CompressionRequest,
    CompressionResult,
    InputBlock,
)


def _make_request(request_id: str = "req-ctx") -> CompressionRequest:
    return CompressionRequest(
        request_id=request_id,
        query="Provide a summary of the context.",
        blocks=[
            InputBlock(
                block_id="b1",
                type="retrieval",
                text="The sky is blue and the grass is green.",
                refs=["ref#1"],
                meta={"retrieval_score": 0.85, "source_id": "doc-1"},
            ),
            InputBlock(
                block_id="b2",
                type="memory",
                text="User prefers concise answers.",
                refs=["mem#1"],
                meta={"retrieval_score": 0.5, "source_id": "mem-1"},
            ),
        ],
        budgets=CompressionBudgets(
            max_output_tokens_total=512,
            hard_cap=True,
        ),
        policy=CompressionPolicy(method_prepass=None),
        engine_version="2026-03-01",
    )


def _build_manifest(result: CompressionResult) -> dict:
    return {
        "compression": {
            "method_id": result.method_id,
            "ratio": result.ratio,
            "hash": result.compression_hash,
            "empty_augmentation": result.report.empty_augmentation,
            "dropped_reason_stats": result.report.dropped_reason_stats,
        }
    }


class TestCtxManifestCompatibility:
    def _compress(self) -> CompressionResult:
        svc = CompressionService()
        result, _ = svc.compress(_make_request())
        return result

    def test_manifest_has_method_id(self):
        result = self._compress()
        manifest = _build_manifest(result)
        assert manifest["compression"]["method_id"] == "extractive.v1"

    def test_manifest_has_ratio(self):
        result = self._compress()
        manifest = _build_manifest(result)
        ratio = manifest["compression"]["ratio"]
        assert isinstance(ratio, float)
        assert 0.0 <= ratio

    def test_manifest_has_hash(self):
        result = self._compress()
        manifest = _build_manifest(result)
        compression_hash = manifest["compression"]["hash"]
        assert isinstance(compression_hash, str)
        assert len(compression_hash) == 64

    def test_manifest_has_empty_augmentation(self):
        result = self._compress()
        manifest = _build_manifest(result)
        assert isinstance(manifest["compression"]["empty_augmentation"], bool)

    def test_manifest_has_dropped_reason_stats(self):
        result = self._compress()
        manifest = _build_manifest(result)
        stats = manifest["compression"]["dropped_reason_stats"]
        assert isinstance(stats, dict)

    def test_manifest_is_deterministic(self):
        svc = CompressionService()
        req = _make_request()
        result_a, _ = svc.compress(req)
        result_b, _ = svc.compress(req)
        manifest_a = _build_manifest(result_a)
        manifest_b = _build_manifest(result_b)
        assert (
            manifest_a["compression"]["method_id"]
            == manifest_b["compression"]["method_id"]
        )
        assert manifest_a["compression"]["hash"] == manifest_b["compression"]["hash"]
        assert manifest_a["compression"]["ratio"] == manifest_b["compression"]["ratio"]

    def test_manifest_hash_is_sha256_length(self):
        result = self._compress()
        manifest = _build_manifest(result)
        # sha256 produces 64 hex characters
        assert len(manifest["compression"]["hash"]) == 64

    def test_manifest_all_required_keys_present(self):
        result = self._compress()
        manifest = _build_manifest(result)
        required = {
            "method_id",
            "ratio",
            "hash",
            "empty_augmentation",
            "dropped_reason_stats",
        }
        assert required <= set(manifest["compression"].keys())
