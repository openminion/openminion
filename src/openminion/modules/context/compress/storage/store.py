import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import CompressTelemetryStore
from .migrations import list_migrations

from ..schemas import CompressionResult


@dataclass
class RunRow:
    run_id: str
    request_id: str
    method_id: str
    input_tokens: int
    output_tokens: int
    ratio: float
    compression_hash: str
    empty_augmentation: bool
    fallback_used: bool
    policy_hash: str
    input_hash: str
    output_hash: str
    engine_version: str
    tokenizer_id: str
    scorer_version: str
    warnings: str  # pipe-joined list


@dataclass
class DroppedReasonRow:
    run_id: str
    reason: str
    count: int


@dataclass
class FailureRow:
    failure_id: str
    request_id: str
    error_code: str
    message: str


@dataclass
class ExplainPayload:
    run_id: str
    request_id: str
    method_id: str
    input_tokens: int
    output_tokens: int
    ratio: float
    compression_hash: str
    empty_augmentation: bool
    empty_reason: str | None
    fallback_used: bool
    dropped_reason_stats: dict[str, int]
    count_by_type: dict[str, int]
    warnings: list[str]
    policy_hash: str
    input_hash: str
    output_hash: str
    engine_version: str
    tokenizer_id: str
    scorer_version: str


def _create_telemetry_schema(record_store: RecordStore, *, postgres: bool) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS compression_runs (
            run_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            method_id TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            ratio DOUBLE PRECISION NOT NULL,
            compression_hash TEXT NOT NULL,
            empty_augmentation INTEGER NOT NULL,
            fallback_used INTEGER NOT NULL,
            policy_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            output_hash TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            tokenizer_id TEXT NOT NULL,
            scorer_version TEXT NOT NULL,
            warnings TEXT NOT NULL DEFAULT ''
        )
        """
    )
    record_store.execute_count(
        f"""
        CREATE TABLE IF NOT EXISTS dropped_reasons (
            id {"INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"},
            run_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            count INTEGER NOT NULL,
            FOREIGN KEY (run_id) REFERENCES compression_runs(run_id)
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS compression_failures (
            failure_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            error_code TEXT NOT NULL,
            message TEXT NOT NULL
        )
        """
    )


class _CompressTelemetryStoreMixin(CompressTelemetryStore):
    """Backend-neutral telemetry store behavior shared across backends."""

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def close(self) -> None:
        BaseModuleStore.close(self)

    @contextmanager
    def _tx(self) -> Iterator[RecordStore]:
        with self._record_store.transaction():
            yield self._record_store

    def record_run(
        self,
        request_id: str,
        result: CompressionResult,
        *,
        run_id: str | None = None,
    ) -> str:
        rid = run_id or str(uuid.uuid4())
        report = result.report
        with self._tx() as store:
            store.execute_count(
                """
                INSERT INTO compression_runs (
                    run_id, request_id, method_id, input_tokens, output_tokens,
                    ratio, compression_hash, empty_augmentation, fallback_used,
                    policy_hash, input_hash, output_hash, engine_version,
                    tokenizer_id, scorer_version, warnings
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rid,
                    request_id,
                    result.method_id,
                    result.input_tokens,
                    result.output_tokens,
                    round(result.ratio, 6),
                    result.compression_hash,
                    int(report.empty_augmentation),
                    int(report.fallback_used),
                    report.policy_hash,
                    report.input_hash,
                    report.output_hash,
                    report.engine_version,
                    report.tokenizer_id,
                    report.scorer_version,
                    "|".join(sorted(result.warnings)),
                ),
            )
            for reason, count in report.dropped_reason_stats.items():
                store.execute_count(
                    "INSERT INTO dropped_reasons (run_id, reason, count) VALUES (?,?,?)",
                    (rid, reason, count),
                )
        return rid

    def record_failure(
        self,
        request_id: str,
        error_code: str,
        message: str,
        *,
        failure_id: str | None = None,
    ) -> str:
        fid = failure_id or str(uuid.uuid4())
        with self._tx() as store:
            store.execute_count(
                "INSERT INTO compression_failures (failure_id, request_id, error_code, message) VALUES (?,?,?,?)",
                (fid, request_id, error_code, message),
            )
        return fid

    def get_run(self, run_id: str) -> RunRow | None:
        rows = self._record_store.query_dicts(
            "SELECT * FROM compression_runs WHERE run_id=?", (run_id,)
        )
        if not rows:
            return None
        return RunRow(**dict(rows[0]))

    def get_dropped_reasons(self, run_id: str) -> list[DroppedReasonRow]:
        rows = self._record_store.query_dicts(
            "SELECT run_id, reason, count FROM dropped_reasons WHERE run_id=?",
            (run_id,),
        )
        return [DroppedReasonRow(**dict(row)) for row in rows]

    def get_explain_payload(self, run_id: str) -> ExplainPayload | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        reasons = self.get_dropped_reasons(run_id)
        dropped_stats = {r.reason: r.count for r in reasons}
        warnings = [w for w in run.warnings.split("|") if w]
        return ExplainPayload(
            run_id=run.run_id,
            request_id=run.request_id,
            method_id=run.method_id,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            ratio=run.ratio,
            compression_hash=run.compression_hash,
            empty_augmentation=bool(run.empty_augmentation),
            empty_reason=None,  # not stored separately yet
            fallback_used=bool(run.fallback_used),
            dropped_reason_stats=dropped_stats,
            count_by_type={},  # requires separate table; deferred to future card
            warnings=warnings,
            policy_hash=run.policy_hash,
            input_hash=run.input_hash,
            output_hash=run.output_hash,
            engine_version=run.engine_version,
            tokenizer_id=run.tokenizer_id,
            scorer_version=run.scorer_version,
        )


class SQLiteTelemetryStore(_CompressTelemetryStoreMixin, BaseModuleSQLiteStore):
    """SQLite-backed telemetry store for compression runs."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        BaseModuleSQLiteStore.__init__(
            self, db_path, wal=wal, record_store=record_store
        )

    def _init_schema(self) -> None:
        with self._lock:
            _create_telemetry_schema(self._record_store, postgres=False)


class PostgresTelemetryStore(_CompressTelemetryStoreMixin, BaseModuleStore):
    """Postgres-backed telemetry store for compression runs."""

    def __init__(self, *, record_store: RecordStore) -> None:
        BaseModuleStore.__init__(self, record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_telemetry_schema(self._record_store, postgres=True)


class TelemetryStore(SQLiteTelemetryStore):
    """Backward-compatible SQLite alias for existing callers."""
