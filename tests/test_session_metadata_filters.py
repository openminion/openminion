"""Session metadata filters and metadata string rows on SQLite (#369).

SQLite keeps its `json_extract` reader; the store maintains the
`session_metadata_strings` rows on SQLite too. The PostgreSQL cases run the
shared scenarios in `tests/integration/test_runtime_storage_postgres.py`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from openminion.modules.storage.backends.postgres import RecordStorePostgres
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.migrations import (
    MigrationError,
    migrate_database,
    run_migrations,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.session_store import metadata_strings
from openminion.modules.storage.runtime.session_store.backend import (
    RuntimeSessionStoreBackend,
)
from openminion.modules.storage.runtime.sqlite import connect_database
from tests._session_metadata_filter_scenarios import (
    check_acp_session_list,
    check_backend_reading_differences,
    check_focus_directory_sessions,
    check_metadata_filters,
)
from tests._session_metadata_strings_scenarios import (
    assert_rows_match_metadata,
    check_failed_writes_roll_back,
    check_odd_metadata_rows,
    check_session_delete_cascade,
    check_write_paths,
    failing_extraction,
    legacy_session_rows,
    migrations_before,
    recorded_metadata_backfill_migrations,
    stored_metadata,
)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[SessionStore]:
    storage = build_runtime_storage(tmp_path / "state" / "openminion.db", env={})
    try:
        yield storage.sessions
    finally:
        storage.close()


def test_sqlite_session_metadata_filters_select_json_string_values(
    sessions: SessionStore,
) -> None:
    check_metadata_filters(sessions)


def test_sqlite_session_metadata_filters_keep_the_legacy_reader(
    sessions: SessionStore,
) -> None:
    check_backend_reading_differences(sessions, postgres=False)


def test_sqlite_acp_session_list_filters_by_surface(
    sessions: SessionStore, tmp_path: Path
) -> None:
    check_acp_session_list(sessions, tmp_path / "acp")


def test_sqlite_focus_directory_sessions_filter_by_working_dir(
    sessions: SessionStore, tmp_path: Path
) -> None:
    check_focus_directory_sessions(sessions, tmp_path / "focus")


def test_sqlite_metadata_strings_hold_odd_metadata_exactly(
    sessions: SessionStore,
) -> None:
    check_odd_metadata_rows(sessions)


def test_sqlite_metadata_strings_follow_every_session_write(
    sessions: SessionStore,
) -> None:
    check_write_paths(sessions)


def test_sqlite_failed_session_writes_roll_back_their_metadata_strings(
    sessions: SessionStore,
) -> None:
    check_failed_writes_roll_back(sessions)


def test_sqlite_session_delete_removes_metadata_strings(
    sessions: SessionStore,
) -> None:
    check_session_delete_cascade(sessions)


def test_sqlite_direct_construction_resolves_the_sqlite_reader(tmp_path: Path) -> None:
    path = tmp_path / "state" / "openminion.db"
    migrate_database(path)
    record_store = RecordStoreSQLite(path)
    try:
        store = SessionStore(record_store)
        assert store._backend.dialect == "sqlite"
        check_metadata_filters(store)
        check_write_paths(store)
    finally:
        record_store.close()
    with closing(connect_database(path)) as connection:
        raw = SessionStore(connection)
        assert raw._backend.dialect == "sqlite"
        check_failed_writes_roll_back(raw)
        assert_rows_match_metadata(raw)
    storage = build_runtime_storage(path, env={})
    try:
        assert storage.sessions._backend.dialect == "sqlite"
    finally:
        storage.close()


def test_session_metadata_match_comes_from_the_record_store_owner() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        key = "k') OR ('1'='1"
        value = "v' OR '1'='1"
        sqlite_backend = RuntimeSessionStoreBackend(connection)
        assert sqlite_backend.session_metadata_match(key, value) == (
            "json_extract(metadata_json, ?) = ?",
            [f"$.{key}", value],
        )
    finally:
        connection.close()

    # A RecordStorePostgres, without an engine: only its type is read.
    postgres_backend = RuntimeSessionStoreBackend(
        RecordStorePostgres.__new__(RecordStorePostgres)
    )
    assert postgres_backend.dialect == "postgres"
    encoded_key = metadata_strings.encode_member(key)
    encoded_value = metadata_strings.encode_member(value)
    assert postgres_backend.session_metadata_match(key, value) == (
        "EXISTS (SELECT 1 FROM session_metadata_strings AS metadata_string "
        "WHERE metadata_string.session_id = sessions.id "
        "AND metadata_string.entry_digest = ? "
        'AND metadata_string.key = CAST(? AS TEXT) COLLATE "C" '
        'AND metadata_string.value = CAST(? AS TEXT) COLLATE "C")',
        [
            metadata_strings.entry_digest(encoded_key, encoded_value),
            encoded_key,
            encoded_value,
        ],
    )
    with pytest.raises(TypeError, match="must be strings, not int"):
        postgres_backend.session_metadata_match("k", 1)  # type: ignore[arg-type]

    other_backend = RuntimeSessionStoreBackend(Mock(spec=RecordStore))
    assert other_backend.dialect is None
    with pytest.raises(TypeError, match="need a sqlite3.Connection"):
        other_backend.session_metadata_match("k", "v")


def _legacy_database(path: Path, extra: list[dict[str, str]] | None = None) -> None:
    """A runtime database at migration 14 holding `legacy_session_rows(2345)`."""
    rows = legacy_session_rows(2345) + list(extra or [])
    with closing(connect_database(path)) as connection:
        run_migrations(connection, migrations=migrations_before(15))
        columns = list(rows[0])
        connection.executemany(
            f"INSERT INTO sessions ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [tuple(row[column] for column in columns) for row in rows],
        )
        connection.commit()


def test_sqlite_metadata_strings_migration_backfills_existing_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "openminion.db"
    _legacy_database(path)
    with closing(connect_database(path)) as connection:
        before = stored_metadata(SessionStore(connection))
    batches: list[int] = []
    result = migrate_database(
        path, migrations=recorded_metadata_backfill_migrations(batches)
    )
    assert result.applied_versions == (15, 16)
    # 2,346 sessions: three batches of up to 1,000.
    assert batches == [1000, 1000, 346, 0]
    with closing(connect_database(path)) as connection:
        store = SessionStore(connection)
        assert stored_metadata(store) == before
        assert_rows_match_metadata(store)


def test_sqlite_metadata_strings_migration_rolls_back_whole_and_reruns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "openminion.db"
    unreadable = {
        "id": "legacy-unreadable",
        "session_key": "legacy-key-unreadable",
        "channel": "console",
        "target": "metadata-strings",
        "metadata_json": '{"surface": ',
        "created_at": "2026-10-25T00:00:00+00:00",
        "updated_at": "2026-10-25T00:00:00+00:00",
    }
    _legacy_database(path, [unreadable])
    with closing(connect_database(path)) as connection:
        before = stored_metadata(SessionStore(connection))

    def assert_not_applied() -> None:
        with closing(connect_database(path)) as connection:
            assert (
                connection.execute("SELECT MAX(version) FROM migrations").fetchone()[0]
                == 14
            )
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'session_metadata_strings'"
                ).fetchone()
                is None
            )
            assert stored_metadata(SessionStore(connection)) == before

    # Unreadable stored metadata, in the last batch.
    with pytest.raises(MigrationError, match="legacy-unreadable"):
        migrate_database(path)
    assert_not_applied()

    with closing(connect_database(path)) as connection:
        connection.execute(
            "UPDATE sessions SET metadata_json = '{}' WHERE id = 'legacy-unreadable'"
        )
        connection.commit()
        before = stored_metadata(SessionStore(connection))
    # A Python failure in the second batch, after the first batch's rows.
    with patch.object(
        metadata_strings, "metadata_string_rows", failing_extraction("legacy-01500")
    ):
        with pytest.raises(RuntimeError, match="extraction failed for legacy-01500"):
            migrate_database(path)
    assert_not_applied()

    assert migrate_database(path).applied_versions == (15, 16)
    with closing(connect_database(path)) as connection:
        store = SessionStore(connection)
        assert stored_metadata(store) == before
        assert_rows_match_metadata(store)
