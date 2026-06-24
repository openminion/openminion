from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.context.compress.schemas import (
    CheckpointStats,
    CheckpointStructuredState,
    CompressionCheckpoint,
    CompressionReport,
    CompressionResult,
)
from openminion.modules.context.compress.storage import build_compress_telemetry_store
from openminion.modules.context.compress.storage.checkpoint_store import (
    PostgresCheckpointStore,
    SQLiteCheckpointStore,
)
from openminion.modules.context.compress.storage.store import (
    PostgresTelemetryStore,
    SQLiteTelemetryStore,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


def _sample_result() -> CompressionResult:
    report = CompressionReport(
        empty_augmentation=False,
        empty_reason=None,
        dropped_reason_stats={"duplicate": 2},
        count_by_type={"evidence": 1},
        fallback_used=False,
        policy_hash="policy-hash",
        input_hash="input-hash",
        output_hash="output-hash",
        engine_version="engine-v1",
        tokenizer_id="tok-v1",
        scorer_version="score-v1",
    )
    return CompressionResult(
        blocks=[],
        report=report,
        method_id="extractive",
        input_tokens=100,
        output_tokens=25,
        ratio=0.25,
        compression_hash="cmp-hash",
        warnings=["warn-a"],
    )


@pytest.fixture(params=_backend_params())
def compress_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            telemetry = SQLiteTelemetryStore(tmp_path / "compress.db")
            checkpoint = SQLiteCheckpointStore(tmp_path / "compress-checkpoints.db")
            stack.callback(telemetry.close)
            stack.callback(checkpoint.close)
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt2_compress")
            )
            telemetry = PostgresTelemetryStore(record_store=record_store)
            checkpoint = PostgresCheckpointStore(record_store=record_store)
        yield backend, telemetry, checkpoint


def test_compress_stores_round_trip(compress_store_case) -> None:
    _backend, telemetry, checkpoint = compress_store_case

    run_id = telemetry.record_run("req-1", _sample_result(), run_id="run-1")
    run = telemetry.get_run(run_id)
    assert run is not None
    assert run.method_id == "extractive"
    assert run.ratio == 0.25

    dropped = telemetry.get_dropped_reasons(run_id)
    assert dropped == [type(dropped[0])(run_id="run-1", reason="duplicate", count=2)]

    explain = telemetry.get_explain_payload(run_id)
    assert explain is not None
    assert explain.dropped_reason_stats == {"duplicate": 2}

    telemetry.record_failure("req-2", "ERR", "failed", failure_id="failure-1")

    checkpoint_id = checkpoint.save_checkpoint(
        CompressionCheckpoint(
            checkpoint_id="cp-1",
            session_id="sess-1",
            created_at="2026-04-01T00:00:00+00:00",
            from_event_id=None,
            to_event_id="evt-1",
            summary_text="summary",
            recent_window_event_ids=["evt-1"],
            structured=CheckpointStructuredState(
                decisions=[],
                constraints=[],
                open_loops=[],
                entities={},
                tool_digests=[],
            ),
            stats=CheckpointStats(
                summary_tokens=7,
                structured_tokens=3,
                total_tokens=10,
                compression_ratio=0.7,
            ),
            version="1.6",
        )
    )
    latest = checkpoint.get_latest_checkpoint("sess-1")
    assert checkpoint_id == "cp-1"
    assert latest is not None
    assert latest.summary_text == "summary"
    assert checkpoint.list_checkpoints("sess-1")[0].checkpoint_id == "cp-1"
    assert checkpoint.delete_checkpoint("cp-1") is True


def test_build_compress_telemetry_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_compress_telemetry_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "compress.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "compress.db",
    )
    try:
        assert isinstance(store, SQLiteTelemetryStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_compress_telemetry_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt2_compress_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_compress_telemetry_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="compress.db",
            ),
            database_path=tmp_path / "compress.db",
        )
        try:
            assert isinstance(store, PostgresTelemetryStore)
        finally:
            store.close()
