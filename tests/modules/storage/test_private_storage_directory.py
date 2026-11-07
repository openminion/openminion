"""Directories created for storage are private; roots the caller owns keep their mode."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from openminion.modules.storage.io import (
    append_jsonl,
    atomic_write_bytes,
    ensure_private_directory,
)
from openminion.modules.storage.record_store import RecordStoreSQLite
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.modules.task.storage.store import SQLiteTaskStore


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@contextmanager
def _umask(mask: int) -> Iterator[None]:
    """Umask 022 is what makes SQLite's own files 0644, the exposure being guarded."""
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _write_and_check_private_database(
    path: Path, connection: sqlite3.Connection
) -> None:
    """Write through the owner, then prove the files stayed umask-created and readable."""
    connection.execute("CREATE TABLE IF NOT EXISTS probe (value TEXT)")
    connection.execute("INSERT INTO probe VALUES ('kept')")
    connection.commit()
    sidecar_modes = [_mode(Path(f"{path}-wal")), _mode(Path(f"{path}-shm"))]
    database_mode = _mode(path)
    connection.close()

    assert sidecar_modes == [0o644, 0o644]
    assert database_mode == 0o644
    assert _mode(path.parent) == 0o700
    reopened = sqlite3.connect(str(path))
    try:
        assert reopened.execute("SELECT value FROM probe").fetchall() == [("kept",)]
    finally:
        reopened.close()


def test_a_created_directory_is_private(tmp_path: Path) -> None:
    target = tmp_path / "state"

    ensure_private_directory(target)

    assert target.is_dir()
    assert _mode(target) == 0o700


def test_a_missing_component_under_an_existing_root_is_private(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o755)

    ensure_private_directory(root / "nested" / "state")

    assert _mode(root) == 0o755
    assert _mode(root / "nested") == 0o700
    assert _mode(root / "nested" / "state") == 0o700


def test_an_existing_directory_keeps_its_mode(tmp_path: Path) -> None:
    target = tmp_path / "shared"
    target.mkdir()
    os.chmod(target, 0o755)

    ensure_private_directory(target)

    assert _mode(target) == 0o755


def test_an_existing_non_directory_is_reported(tmp_path: Path) -> None:
    target = tmp_path / "state"
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        ensure_private_directory(target)


def test_a_directory_created_by_another_creator_keeps_its_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A creator that wins the race owns the directory; this call must not chmod it."""
    target = tmp_path / "state"
    original = Path.mkdir

    def foreign_creator(self: Path, mode: int = 0o777, *args: object) -> None:
        original(self, 0o755)
        raise FileExistsError(str(self))

    monkeypatch.setattr(Path, "mkdir", foreign_creator)

    ensure_private_directory(target)

    assert _mode(target) == 0o755


def test_a_file_created_during_the_race_is_a_failure_not_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FileExistsError` from a non-directory is a creation failure, not a race."""
    target = tmp_path / "state"
    original = Path.mkdir

    def foreign_file(self: Path, mode: int = 0o777, *args: object) -> None:
        original(self, 0o755)
        self.rmdir()
        self.write_text("not a directory", encoding="utf-8")
        raise FileExistsError(str(self))

    monkeypatch.setattr(Path, "mkdir", foreign_file)

    with pytest.raises(NotADirectoryError):
        ensure_private_directory(target)


def test_record_store_sqlite_creates_a_private_database_directory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data" / "state" / "records.db"
    with _umask(0o022):
        store = RecordStoreSQLite(path)
    try:
        _write_and_check_private_database(path, store.connection)
    finally:
        store.close()


def test_connect_database_creates_a_private_database_directory(tmp_path: Path) -> None:
    path = tmp_path / "data" / "state" / "openminion.db"
    with _umask(0o022):
        connection = connect_database(path)

    _write_and_check_private_database(path, connection)


def test_a_module_store_creates_a_private_database_directory(tmp_path: Path) -> None:
    path = tmp_path / "data" / "state" / "task.db"
    with _umask(0o022):
        store = SQLiteTaskStore(path)
    try:
        assert _mode(path.parent) == 0o700
        assert _mode(path) == 0o644
    finally:
        store.close()
    reopened = sqlite3.connect(str(path))
    try:
        assert reopened.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
    finally:
        reopened.close()


def test_a_caller_owned_root_keeps_its_mode_and_still_works(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o755)
    path = root / "records.db"

    store = RecordStoreSQLite(path)
    try:
        store.connection.execute("CREATE TABLE probe (value TEXT)")
        store.connection.execute("INSERT INTO probe VALUES ('kept')")
        store.connection.commit()
    finally:
        store.close()

    assert _mode(root) == 0o755
    reopened = sqlite3.connect(str(path))
    try:
        assert reopened.execute("SELECT value FROM probe").fetchall() == [("kept",)]
    finally:
        reopened.close()


def test_a_written_file_sits_in_a_private_directory(tmp_path: Path) -> None:
    target = tmp_path / "data" / "report.bin"

    atomic_write_bytes(target, b"report")

    assert _mode(target.parent) == 0o700
    assert target.read_bytes() == b"report"


def test_an_appended_event_log_sits_in_a_private_directory(tmp_path: Path) -> None:
    target = tmp_path / "logs" / "events.jsonl"

    append_jsonl(target, {"event": "turn"})

    assert _mode(target.parent) == 0o700
    assert '"event":"turn"' in target.read_text(encoding="utf-8")
