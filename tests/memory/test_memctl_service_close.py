"""memctl closes the memory service that `_get_service` creates.

The cyclic collector is disabled while each test runs: the store, audit sink and
service hold a reference cycle, so collection must not be what closes them.
"""

from __future__ import annotations

import gc
import json
import os
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openminion.modules.memory import cli as memory_cli
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    SQLiteMemoryAuditSink,
    default_memory_audit_db_path,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


@pytest.fixture(autouse=True)
def no_cyclic_gc() -> Iterator[None]:
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


class Recorder:
    """The services, sinks and stores memctl creates, and their `close` calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.services: list[MemoryService] = []
        self.closes: Counter[int] = Counter()
        recorder = self

        class RecordingService(MemoryService):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                recorder.services.append(self)

            def close(self) -> None:
                recorder.closes[id(self)] += 1
                super().close()

        class RecordingSink(SQLiteMemoryAuditSink):
            def close(self) -> None:
                recorder.closes[id(self)] += 1
                super().close()

        real_store_close = SQLiteMemoryStore.close

        def store_close(store: SQLiteMemoryStore) -> None:
            recorder.closes[id(store)] += 1
            real_store_close(store)

        monkeypatch.setattr(memory_cli, "MemoryService", RecordingService)
        monkeypatch.setattr(memory_cli, "SQLiteMemoryAuditSink", RecordingSink)
        monkeypatch.setattr(SQLiteMemoryStore, "close", store_close)

    def parts(self, service: MemoryService) -> tuple[Any, Any]:
        audited = service._store  # noqa: SLF001
        assert isinstance(audited, AuditedMemoryStore)
        return audited._store, audited._sink  # noqa: SLF001

    def close_counts(self, service: MemoryService) -> tuple[int, int, int]:
        store, sink = self.parts(service)
        return self.closes[id(service)], self.closes[id(sink)], self.closes[id(store)]

    def assert_closed_once(self) -> None:
        """One service per command, closed once with its sink and store."""
        [service] = self.services
        assert self.close_counts(service) == (1, 1, 1)
        # A second close is a no-op: the sink and the store stay closed once.
        service.close()
        assert self.close_counts(service) == (2, 1, 1)


def open_connections(*paths: Path) -> list[str]:
    """Files among `paths` that an open sqlite3 connection still holds."""
    wanted = {os.path.normcase(os.path.abspath(path)) for path in paths}
    held = []
    for obj in gc.get_objects():
        if type(obj) is not sqlite3.Connection:
            continue
        try:
            files = [row[2] for row in obj.execute("PRAGMA database_list") if row[2]]
        except sqlite3.ProgrammingError:  # closed
            continue
        held.extend(
            name for name in files if os.path.normcase(os.path.abspath(name)) in wanted
        )
    return held


def audit_event_types(audit_path: Path) -> list[str]:
    with closing(sqlite3.connect(audit_path)) as conn:
        rows = conn.execute(
            "SELECT event_type FROM memory_audit_events ORDER BY timestamp"
        ).fetchall()
    return [row[0] for row in rows]


def seed(db_path: Path, *candidates: tuple[str, str]) -> None:
    store = SQLiteMemoryStore(db_path)
    try:
        store.put(
            MemoryRecord(
                id="r1",
                scope="session:s1",
                type="fact",
                content="the sky is blue",
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            )
        )
        for candidate_id, status in candidates:
            store.candidate_put(
                MemoryCandidate(
                    candidate_id=candidate_id,
                    session_id="s1",
                    proposed_scope="session:s1",
                    type="fact",
                    content=f"proposed fact {candidate_id}",
                    status=status,
                )
            )
    finally:
        store.close()


def stored_candidate_status(db_path: Path, candidate_id: str) -> str:
    store = SQLiteMemoryStore(db_path)
    try:
        return str(store.candidate_get(candidate_id).status)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("args", "output", "status", "audited"),
    [
        (
            ["approve", "c1", "--reviewer", "agent"],
            "Approved: c1",
            "approved",
            ["memory.candidate.update"],
        ),
        (
            ["reject", "c1", "--reviewer", "agent", "--note", "not relevant"],
            "Rejected: c1",
            "rejected",
            ["memory.candidate.update"],
        ),
        (
            ["promote-approved", "--session-id", "s1", "--target-scope", "global:all"],
            "Promoted: 1, Failed: 0",
            "promoted",
            ["memory.candidate.promote"],
        ),
    ],
)
def test_write_command_closes_its_service_and_audit_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    output: str,
    status: str,
    audited: list[str],
) -> None:
    db_path = tmp_path / "test.db"
    audit_path = default_memory_audit_db_path(db_path)
    seed(db_path, ("c1", "approved" if args[0] == "promote-approved" else "proposed"))
    recorder = Recorder(monkeypatch)

    result = CliRunner().invoke(memory_cli._build_app(), [*args, "--db", str(db_path)])

    assert result.exit_code == 0, result.output
    assert output in result.output
    assert open_connections(db_path, audit_path) == []
    recorder.assert_closed_once()
    assert stored_candidate_status(db_path, "c1") == status
    assert audit_event_types(audit_path) == audited


def test_read_command_closes_its_service_without_opening_the_audit_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    seed(db_path)
    recorder = Recorder(monkeypatch)

    result = CliRunner().invoke(
        memory_cli._build_app(), ["get", "r1", "--json", "--db", str(db_path)]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["content"] == "the sky is blue"
    assert not default_memory_audit_db_path(db_path).exists()
    assert open_connections(db_path) == []
    recorder.assert_closed_once()


def test_failing_write_command_closes_its_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    seed(db_path)
    recorder = Recorder(monkeypatch)

    result = CliRunner().invoke(
        memory_cli._build_app(),
        ["approve", "missing", "--reviewer", "agent", "--db", str(db_path)],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert open_connections(db_path, default_memory_audit_db_path(db_path)) == []
    recorder.assert_closed_once()


def test_command_failing_after_an_audited_write_closes_its_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    audit_path = default_memory_audit_db_path(db_path)
    seed(db_path, ("c1", "approved"), ("c2", "approved"))
    real_promote = SQLiteMemoryStore.promote_candidate

    def promote(store: SQLiteMemoryStore, candidate_id: str, target_scope: str) -> Any:
        if candidate_id == "c2":
            raise RuntimeError("store failure")
        return real_promote(store, candidate_id, target_scope)

    monkeypatch.setattr(SQLiteMemoryStore, "promote_candidate", promote)
    recorder = Recorder(monkeypatch)

    result = CliRunner().invoke(
        memory_cli._build_app(),
        [
            "promote-approved",
            "--session-id",
            "s1",
            "--target-scope",
            "global:all",
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 1
    assert "Promoted: 1, Failed: 1" in result.output
    assert open_connections(db_path, audit_path) == []
    recorder.assert_closed_once()
    assert audit_event_types(audit_path) == ["memory.candidate.promote"]


def test_command_raising_an_unexpected_error_closes_its_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    seed(db_path)

    def history(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("history failed")

    monkeypatch.setattr(SQLiteMemoryStore, "history", history)
    recorder = Recorder(monkeypatch)

    result = CliRunner().invoke(
        memory_cli._build_app(),
        [
            "history",
            "--scope",
            "session:s1",
            "--type",
            "fact",
            "--key",
            "k",
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert open_connections(db_path) == []
    recorder.assert_closed_once()


def test_service_built_outside_a_command_is_closed_by_its_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    audit_path = default_memory_audit_db_path(db_path)
    seed(db_path, ("c1", "proposed"))
    recorder = Recorder(monkeypatch)

    service = memory_cli._get_service(str(db_path))
    service.candidate_update("c1", {"status": "approved"})

    # Nothing closes it for the caller.
    assert len(open_connections(audit_path)) == 1
    assert recorder.close_counts(service) == (0, 0, 0)
    service.close()
    assert open_connections(db_path, audit_path) == []
    assert recorder.close_counts(service) == (1, 1, 1)
    service.close()
    assert recorder.close_counts(service) == (2, 1, 1)
    assert stored_candidate_status(db_path, "c1") == "approved"


def test_command_leaves_a_borrowed_service_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    audit_path = default_memory_audit_db_path(db_path)
    seed(db_path, ("c1", "proposed"))
    sink = SQLiteMemoryAuditSink(audit_path)
    borrowed = MemoryService(AuditedMemoryStore(SQLiteMemoryStore(db_path), sink=sink))
    monkeypatch.setattr(memory_cli, "_get_service", lambda *_args, **_kwargs: borrowed)
    recorder = Recorder(monkeypatch)
    try:
        result = CliRunner().invoke(
            memory_cli._build_app(),
            ["approve", "c1", "--reviewer", "agent", "--db", str(db_path)],
        )

        assert result.exit_code == 0, result.output
        assert recorder.services == []
        # The borrowed service, its store and its sink are still in use.
        assert len(open_connections(audit_path)) == 1
        assert borrowed.candidate_get("c1").status == "approved"
    finally:
        sink.close()
    assert open_connections(audit_path) == []
