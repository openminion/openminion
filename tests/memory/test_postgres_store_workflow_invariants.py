from __future__ import annotations

from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


class _RecordingLock:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> None:
        self._events.append("lock_enter")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._events.append("lock_exit")


class _ForbiddenLock:
    def __enter__(self) -> None:
        raise AssertionError("basic candidate methods must not acquire _lock")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class _RecordingTxn:
    def __init__(self, events: list[str], connection: object) -> None:
        self._events = events
        self._connection = connection

    def __enter__(self) -> object:
        self._events.append("txn_enter")
        return self._connection

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._events.append("txn_exit")


class _RecordingEngine:
    def __init__(self, events: list[str], connection: object) -> None:
        self._events = events
        self._connection = connection

    def begin(self) -> _RecordingTxn:
        self._events.append("engine_begin")
        return _RecordingTxn(self._events, self._connection)


class _ForbiddenEngine:
    def begin(self) -> object:
        raise AssertionError("helper must reuse caller connection")


def _memory_record(record_id: str) -> MemoryRecord:
    now = "2026-04-03T00:00:00+00:00"
    return MemoryRecord(
        id=record_id,
        scope="agent:postgres",
        type="fact",
        key="pref:test",
        title="Testing preference",
        content={"text": "Prefer deterministic tests."},
        tags=["tests"],
        entities=["Agent"],
        created_at=now,
        updated_at=now,
    )


def _memory_candidate(candidate_id: str) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="sess-1",
        proposed_scope="agent:postgres",
        type="fact",
        key="pref:test",
        title="Testing preference",
        content={"text": "Prefer deterministic tests."},
        tags=["tests"],
        entities=["Agent"],
        status="approved",
    )


def _new_store_shell() -> PostgresMemoryStore:
    return object.__new__(PostgresMemoryStore)


def test_put_acquires_lock_before_transaction_and_adds_artifact_refs_after_close() -> (
    None
):
    events: list[str] = []
    txn_connection = object()
    store = _new_store_shell()
    store._lock = _RecordingLock(events)
    store._engine = _RecordingEngine(events, txn_connection)
    store._insert_record = lambda connection, **_: events.append(
        "insert_with_caller_connection"
        if connection is txn_connection
        else "insert_with_wrong_connection"
    )
    store._upsert_entities = lambda connection, **_: events.append(
        "entities_with_caller_connection"
        if connection is txn_connection
        else "entities_with_wrong_connection"
    )
    store._add_artifact_refs = lambda *, owner_id, ref_values: events.append("add_refs")

    assert store.put(_memory_record("record-1")) == "record-1"

    assert events == [
        "lock_enter",
        "engine_begin",
        "txn_enter",
        "insert_with_caller_connection",
        "entities_with_caller_connection",
        "txn_exit",
        "lock_exit",
        "add_refs",
    ]


def test_basic_candidate_put_does_not_acquire_store_lock() -> None:
    events: list[str] = []
    txn_connection = object()
    store = _new_store_shell()
    store._lock = _ForbiddenLock()
    store._engine = _RecordingEngine(events, txn_connection)
    store.candidate_get = lambda _candidate_id: None
    store._execute = lambda _sql, _params=None, *, connection=None: events.append(
        "execute_with_caller_connection"
        if connection is txn_connection
        else "execute_with_wrong_connection"
    )
    store._add_artifact_refs = lambda *, owner_id, ref_values: events.append("add_refs")

    assert store.candidate_put(_memory_candidate("candidate-1")) == "candidate-1"

    assert events == [
        "engine_begin",
        "txn_enter",
        "execute_with_caller_connection",
        "txn_exit",
        "add_refs",
    ]


def test_transaction_scoped_helpers_reuse_caller_connection() -> None:
    executed: list[tuple[str, object | None]] = []
    caller_connection = object()
    store = _new_store_shell()
    store._engine = _ForbiddenEngine()
    store._execute = lambda sql, _params=None, *, connection=None: executed.append(
        (str(sql), connection)
    )

    store._apply_supersession(
        caller_connection,
        old_record_id="old-record",
        new_record_id="new-record",
        now_iso="2026-04-03T00:00:00+00:00",
        valid_to_iso="2026-04-03T00:00:00+00:00",
        reason="test",
    )
    store._upsert_entities(
        caller_connection,
        record_id="new-record",
        scope="agent:postgres",
        record_type="fact",
        entities=["Agent", "", "Agent 2"],
        created_at="2026-04-03T00:00:00+00:00",
    )

    assert len(executed) == 4
    assert [connection for _, connection in executed] == [caller_connection] * 4
    assert all(
        "memory_records" in sql or "memory_entities" in sql for sql, _ in executed
    )
