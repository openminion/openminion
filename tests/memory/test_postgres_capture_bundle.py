from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureBundleIntegrityError,
    CaptureBundleReceipt,
    CaptureCandidateInput,
    capture_output_id,
)
from openminion.modules.memory.storage.audit import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


def _bundle(
    *,
    capture_id: str = "capture-1",
    content: str = "Use concise replies.",
) -> CaptureBundleInput:
    return CaptureBundleInput(
        capture_id=capture_id,
        root_turn_id="turn-1",
        session_id="session-1",
        agent_id="agent-1",
        candidates=(
            CaptureCandidateInput(
                kind="user_preference",
                normalized_key="preference:concise",
                title="Response preference",
                content=content,
                tags=("preference",),
                confidence=0.8,
            ),
            CaptureCandidateInput(
                kind="unsupported",
                normalized_key="ignored",
                title="Ignored",
                content="Ignored",
            ),
        ),
    )


class _Transaction(AbstractContextManager[object]):
    def __init__(self, engine: "_Engine") -> None:
        self._engine = engine
        self.connection = SimpleNamespace(
            receipts=deepcopy(engine.receipts),
            candidates=deepcopy(engine.candidates),
        )

    def __enter__(self) -> object:
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self._engine.receipts = self.connection.receipts
            self._engine.candidates = self.connection.candidates
            self._engine.commit_count += 1
        else:
            self._engine.rollback_count += 1


class _Engine:
    def __init__(self) -> None:
        self.receipts: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.fail_candidate_id: str | None = None

    def begin(self) -> _Transaction:
        self.begin_count += 1
        return _Transaction(self)


def _store_shell(engine: _Engine) -> PostgresMemoryStore:
    store = object.__new__(PostgresMemoryStore)
    store._engine = engine
    store._lock = threading.RLock()

    def _fetchone(
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        connection: Any = None,
    ) -> dict[str, Any] | None:
        assert connection is not None
        assert "memory_capture_bundle_receipts" in sql
        return connection.receipts.get(str((params or {})["capture_id"]))

    def _execute(
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        connection: Any = None,
    ) -> int:
        assert connection is not None
        values = dict(params or {})
        if "pg_advisory_xact_lock" in sql:
            return 1
        if "INSERT INTO memory_candidates" in sql:
            candidate_id = str(values["candidate_id"])
            if candidate_id == engine.fail_candidate_id:
                raise RuntimeError("injected candidate failure")
            connection.candidates[candidate_id] = values
            return 1
        if "INSERT INTO memory_capture_bundle_receipts" in sql:
            connection.receipts[str(values["capture_id"])] = {
                **values,
                "output_ids_json": values["output_ids_json"],
            }
            return 1
        raise AssertionError(f"unexpected SQL: {sql}")

    store._fetchone = _fetchone
    store._execute = _execute
    return store


def test_postgres_capture_bundle_commit_replay_and_conflict() -> None:
    engine = _Engine()
    store = _store_shell(engine)
    bundle = _bundle()

    first = store.apply_capture_bundle(bundle)
    replay = store.apply_capture_bundle(bundle)

    assert replay == first
    assert first.disposition == "succeeded"
    assert tuple(engine.candidates) == first.output_ids
    assert engine.begin_count == 2
    assert engine.commit_count == 2
    assert engine.rollback_count == 0
    candidate = engine.candidates[first.output_ids[0]]
    assert candidate["proposed_scope"] == "agent:agent-1"
    assert candidate["source"] == "agent_inferred"

    with pytest.raises(CaptureBundleIntegrityError):
        store.apply_capture_bundle(_bundle(content="Use detailed replies."))

    assert tuple(engine.candidates) == first.output_ids
    assert engine.begin_count == 3
    assert engine.commit_count == 2
    assert engine.rollback_count == 1


def test_postgres_capture_bundle_rolls_back_candidates_and_receipt_together() -> None:
    engine = _Engine()
    store = _store_shell(engine)
    bundle = CaptureBundleInput(
        capture_id="capture-rollback",
        root_turn_id="turn-1",
        session_id="session-1",
        agent_id="agent-1",
        candidates=(
            CaptureCandidateInput(
                kind="fact",
                normalized_key="fact:first",
                title="First",
                content="First fact",
            ),
            CaptureCandidateInput(
                kind="task",
                normalized_key="task:second",
                title="Second",
                content="Second task",
            ),
        ),
    )
    engine.fail_candidate_id = capture_output_id(
        capture_id=bundle.capture_id,
        normalized_key="task:second",
        ordinal=1,
    )

    with pytest.raises(RuntimeError, match="injected candidate failure"):
        store.apply_capture_bundle(bundle)

    assert engine.candidates == {}
    assert engine.receipts == {}
    assert engine.begin_count == 1
    assert engine.commit_count == 0
    assert engine.rollback_count == 1


def test_postgres_bootstrap_creates_capture_receipt_table(monkeypatch) -> None:
    statements: list[str] = []

    class _Connection:
        def exec_driver_sql(self, sql: str, *_args: object) -> None:
            statements.append(sql)

    class _BootstrapEngine:
        def begin(self) -> AbstractContextManager[object]:
            return _Begin()

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def migrate(self, *, target: str) -> SimpleNamespace:
            assert target == "head"
            return SimpleNamespace(success=True, error=None)

    class _Begin:
        def __enter__(self) -> _Connection:
            return _Connection()

        def __exit__(self, *_args: object) -> None:
            return None

    engine = _BootstrapEngine()
    monkeypatch.setattr(
        "openminion.modules.memory.storage.postgres.store.MigrationRunner",
        _Runner,
    )
    store = object.__new__(PostgresMemoryStore)
    store._engine = engine

    store._bootstrap_schema(Path("/tmp/postgres-memory-placeholder"))

    assert any("memory_capture_bundle_receipts" in sql for sql in statements)


def test_audited_capture_bundle_emits_one_content_free_event() -> None:
    bundle = _bundle()
    receipt = CaptureBundleReceipt(
        capture_id=bundle.capture_id,
        report_hash=bundle.report_hash,
        result_hash="result-hash",
        output_ids=("candidate-1",),
        disposition="succeeded",
        committed_at="2026-09-01T00:00:00+00:00",
    )
    backend = SimpleNamespace(apply_capture_bundle=lambda _bundle: receipt)
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(backend, sink=sink)

    assert store.apply_capture_bundle(bundle) == receipt
    assert store.apply_capture_bundle(bundle) == receipt

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_type == "memory.capture_bundle.commit"
    assert event.target_id == bundle.capture_id
    assert event.session_id == bundle.session_id
    assert event.details == {
        "report_hash": receipt.report_hash,
        "result_hash": receipt.result_hash,
        "disposition": "succeeded",
        "output_count": 1,
    }
    serialized = str(event.to_dict())
    assert "Use concise replies." not in serialized
    assert "Response preference" not in serialized
    assert "candidate-1" not in serialized
