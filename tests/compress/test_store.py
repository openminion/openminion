from __future__ import annotations

import pytest

from openminion.modules.context.compress.schemas import (
    CompressionReport,
    CompressionResult,
    CompressedBlock,
)
from openminion.modules.context.compress.storage.store import TelemetryStore


def _make_result(
    method_id: str = "extractive.v1",
    ratio: float = 0.5,
    compression_hash: str = "abc123",
    empty_aug: bool = False,
    dropped: dict | None = None,
    warnings: list | None = None,
) -> CompressionResult:
    report = CompressionReport(
        empty_augmentation=empty_aug,
        empty_reason=None,
        dropped_reason_stats=dropped or {"budget": 1},
        count_by_type={"retrieval": 1},
        fallback_used=False,
        policy_hash="ph1",
        input_hash="ih1",
        output_hash="oh1",
        engine_version="2026-03-01",
        tokenizer_id="whitespace.v1",
        scorer_version="v1.0",
    )
    block = CompressedBlock(
        block_id="b1",
        type="retrieval",
        text="hello world",
        refs=["ref-1"],
        unit_refs=["b1:0"],
        compression_meta={"method_id": method_id},
    )
    return CompressionResult(
        blocks=[block],
        report=report,
        method_id=method_id,
        input_tokens=100,
        output_tokens=50,
        ratio=ratio,
        compression_hash=compression_hash,
        warnings=warnings or [],
    )


class TestTelemetryStore:
    def test_record_run_returns_run_id(self):
        store = TelemetryStore()
        result = _make_result()
        run_id = store.record_run("req-1", result)
        assert isinstance(run_id, str)
        assert len(run_id) > 0

    def test_record_run_persists_method_and_ratio_and_hash(self):
        store = TelemetryStore()
        result = _make_result(
            method_id="extractive.v1", ratio=0.35, compression_hash="deadbeef"
        )
        run_id = store.record_run("req-2", result)

        row = store.get_run(run_id)
        assert row is not None
        assert row.method_id == "extractive.v1"
        assert row.ratio == pytest.approx(0.35, abs=1e-6)
        assert row.compression_hash == "deadbeef"
        assert row.request_id == "req-2"

    def test_record_run_persists_dropped_reasons(self):
        store = TelemetryStore()
        result = _make_result(dropped={"budget": 3, "source_cap": 1})
        run_id = store.record_run("req-3", result)

        reasons = store.get_dropped_reasons(run_id)
        reason_map = {r.reason: r.count for r in reasons}
        assert reason_map["budget"] == 3
        assert reason_map["source_cap"] == 1

    def test_record_failure(self):
        store = TelemetryStore()
        fid = store.record_failure("req-4", "BUDGET_EXCEEDED", "total cap exceeded")
        assert isinstance(fid, str)

    def test_get_run_returns_none_for_missing(self):
        store = TelemetryStore()
        assert store.get_run("nonexistent-id") is None

    def test_get_explain_payload_has_all_fields(self):
        store = TelemetryStore()
        result = _make_result(
            method_id="extractive.v1",
            ratio=0.4,
            compression_hash="hash123",
            empty_aug=False,
            dropped={"budget": 2, "irrelevant": 1},
            warnings=["prepass_unavailable:x"],
        )
        run_id = store.record_run("req-5", result)

        payload = store.get_explain_payload(run_id)
        assert payload is not None
        assert payload.run_id == run_id
        assert payload.method_id == "extractive.v1"
        assert payload.compression_hash == "hash123"
        assert payload.dropped_reason_stats == {"budget": 2, "irrelevant": 1}
        assert payload.warnings == ["prepass_unavailable:x"]
        assert payload.fallback_used is False
        assert payload.empty_augmentation is False

    def test_get_explain_payload_returns_none_for_missing(self):
        store = TelemetryStore()
        assert store.get_explain_payload("not-a-run") is None

    def test_custom_run_id(self):
        store = TelemetryStore()
        result = _make_result()
        run_id = store.record_run("req-6", result, run_id="my-fixed-id")
        assert run_id == "my-fixed-id"
        row = store.get_run("my-fixed-id")
        assert row is not None
