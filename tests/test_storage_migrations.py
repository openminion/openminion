import sqlite3
import tempfile
import threading
import unittest
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    Migration,
    MigrationError,
    migrate_database,
    run_migrations,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from tests._room_participant_scenarios import (
    COUNTED_JOIN_SEQ_SQL,
    LEGACY_ORDER,
    PRE_UPGRADE_ORDER_SQL,
    check_upgraded_join_order,
    legacy_participant_rows,
)

PRE_APPEND_SEQ_MIGRATIONS = tuple(
    migration for migration in DEFAULT_MIGRATIONS if migration.version < 12
)
T0 = "2026-10-22T00:59:00+00:00"
T1 = "2026-10-22T01:00:00+00:00"
T2 = "2026-10-22T01:01:00+00:00"
CONTEXT_COLUMNS = (
    "session_id, rolling_summary, compacted_until_rowid, "
    "compacted_until_created_at, compacted_until_message_id, "
    "compacted_message_count"
)


class StorageMigrationTests(unittest.TestCase):
    def test_migrate_database_creates_expected_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "state" / "openminion.db"

            result = migrate_database(database_path)
            self.assertEqual(result.current_version, 14)
            self.assertEqual(
                result.applied_versions,
                (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14),
            )

            with sqlite3.connect(str(database_path)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }

                self.assertIn("migrations", tables)
                self.assertIn("sessions", tables)
                self.assertIn("messages", tables)
                self.assertIn("events", tables)
                self.assertIn("idempotency_keys", tables)
                self.assertIn("session_contexts", tables)
                self.assertIn("daemon_registry", tables)
                self.assertIn("daemon_heartbeats", tables)
                self.assertIn("a2a_jobs", tables)
                self.assertIn("memory_records", tables)
                self.assertIn("memory_vectors", tables)
                self.assertIn("room_participants", tables)
                self.assertIn("session_turn_leases", tables)
                self.assertIn("event_run_facts", tables)
                indexes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
                self.assertIn("idx_event_run_facts_session", indexes)
                self.assertIn("idx_event_run_facts_run", indexes)
                self.assertIn("idx_room_participants_session_join_seq", indexes)

                session_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
                }
                self.assertTrue(
                    {
                        "status",
                        "last_activity_at",
                        "closed_at",
                        "expires_at",
                        "active_agent_id",
                    }.issubset(session_columns)
                )
                session_context_columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(session_contexts)"
                    ).fetchall()
                }
                self.assertIn("summary_short", session_context_columns)
                message_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(messages)").fetchall()
                }
                self.assertIn("append_seq", message_columns)
                participant_columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(room_participants)"
                    ).fetchall()
                }
                self.assertIn("join_seq", participant_columns)

                migration_rows = conn.execute(
                    "SELECT version, name FROM migrations"
                ).fetchall()
                self.assertEqual(
                    migration_rows,
                    [
                        (1, "bootstrap_core_tables"),
                        (2, "add_session_contexts_table"),
                        (3, "add_agent_runtime_and_a2a_tables"),
                        (4, "add_vector_memory_tables"),
                        (5, "add_message_conversation_id"),
                        (6, "add_message_thread_attach_ids"),
                        (7, "add_session_lifecycle_columns"),
                        (8, "add_session_context_summary_short"),
                        (9, "add_room_participants_and_active_agent"),
                        (10, "add_session_turn_leases"),
                        (11, "add_canonical_events_and_capture_holds"),
                        (12, "add_message_append_seq"),
                        (13, "add_event_run_facts"),
                        (14, "add_room_participant_join_seq"),
                    ],
                )

    def test_migrate_database_is_idempotent_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "state" / "openminion.db"

            first = migrate_database(database_path)
            second = migrate_database(database_path)

            self.assertEqual(first.current_version, 14)
            self.assertEqual(
                first.applied_versions,
                (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14),
            )
            self.assertEqual(second.current_version, 14)
            self.assertEqual(second.applied_versions, ())

            with sqlite3.connect(str(database_path)) as conn:
                migration_count = conn.execute(
                    "SELECT COUNT(*) FROM migrations"
                ).fetchone()[0]
                self.assertEqual(migration_count, 14)

    def test_run_migrations_rolls_back_failed_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "state" / "openminion.db"
            connection = connect_database(database_path)
            try:
                bad_migrations = (
                    Migration(
                        version=1,
                        name="broken",
                        statements=(
                            "CREATE TABLE rollback_check (id INTEGER PRIMARY KEY)",
                            "CREATE TABLE syntax_error (",
                        ),
                    ),
                )

                with self.assertRaises(MigrationError):
                    run_migrations(connection, migrations=bad_migrations)

                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("migrations", tables)
                self.assertNotIn("rollback_check", tables)

                migration_count = connection.execute(
                    "SELECT COUNT(*) FROM migrations"
                ).fetchone()[0]
                self.assertEqual(migration_count, 0)
            finally:
                connection.close()

    def test_connect_database_allows_cross_thread_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "state" / "openminion.db"
            connection = connect_database(database_path)
            try:
                errors: list[str] = []

                def _worker() -> None:
                    try:
                        connection.execute("SELECT 1").fetchone()
                    except (
                        Exception
                    ) as exc:  # pragma: no cover - assertion captures message
                        errors.append(str(exc))

                worker = threading.Thread(target=_worker)
                worker.start()
                worker.join(timeout=5)
                self.assertEqual(errors, [])
            finally:
                connection.close()


def test_migrate_openminion_data_into_openminion(tmp_path: Path) -> None:
    from openminion.services.bootstrap.migration import migrate_data_root

    home_root = tmp_path / "runtime"
    legacy_root = home_root / ".openminion-data"
    data_root = home_root / ".openminion"
    legacy_state = legacy_root / "state"
    legacy_tmp = home_root / ".tmp"

    legacy_state.mkdir(parents=True, exist_ok=True)
    legacy_tmp.mkdir(parents=True, exist_ok=True)
    (legacy_state / "openminion.db").write_text("legacy-db", encoding="utf-8")
    (legacy_tmp / "scratch.txt").write_text("scratch", encoding="utf-8")

    report = migrate_data_root(home_root=home_root, data_root=data_root, dry_run=False)
    assert report.items

    assert (data_root / "state" / "openminion.db").read_text(
        encoding="utf-8"
    ) == "legacy-db"
    assert (data_root / "runtime" / "scratch.txt").read_text(
        encoding="utf-8"
    ) == "scratch"
    assert not legacy_root.exists()

    # Idempotent second run
    report_again = migrate_data_root(
        home_root=home_root, data_root=data_root, dry_run=False
    )
    assert report_again.items is not None


def _legacy_runtime_database(
    path: Path,
    *,
    messages: Sequence[tuple[str, str, str, str]],
    contexts: Sequence[tuple[str, str, int, str, str, int]] = (),
) -> None:
    """Write a pre-append_seq database; messages are (id, session, body, created_at)."""
    migrate_database(path, migrations=PRE_APPEND_SEQ_MIGRATIONS)
    session_ids = sorted({row[1] for row in messages} | {row[0] for row in contexts})
    with closing(sqlite3.connect(str(path))) as conn:
        conn.executemany(
            "INSERT INTO sessions(id, session_key, channel, target) "
            "VALUES (?, ?, 'console', ?)",
            [(item, f"key-{item}", item) for item in session_ids],
        )
        conn.executemany(
            "INSERT INTO messages(id, session_id, role, body, created_at) "
            "VALUES (?, ?, 'inbound', ?, ?)",
            messages,
        )
        conn.executemany(
            f"INSERT INTO session_contexts({CONTEXT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            contexts,
        )
        conn.commit()


def _context_row(path: Path, session_id: str) -> tuple[object, ...]:
    with closing(sqlite3.connect(str(path))) as conn:
        return conn.execute(
            f"SELECT {CONTEXT_COLUMNS}, version, updated_at FROM session_contexts "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()


def _visible_bodies(path: Path, session_id: str) -> list[str]:
    with closing(connect_database(path)) as conn:
        store = SessionStore(conn)
        context = store.get_session_context(session_id=session_id)
        cursor = context.compacted_until_rowid if context else 0
        return [
            item.body
            for item in store.list_messages_after_rowid(
                session_id=session_id, after_rowid=cursor
            )
        ]


def test_append_seq_migration_backfills_migration_time_rowid_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "openminion.db"
    # Append order; inside each created_at the ids sort in reverse.
    messages = [
        ("f" * 32, "s1", "s1-first", T1),
        ("e" * 32, "s2", "s2-first", T1),
        ("c" * 32, "s1", "s1-second", T1),
        ("8" * 32, "s1", "s1-third", T1),
        ("5" * 32, "s2", "s2-second", T1),
        ("0" * 32, "s1", "s1-fourth", T2),
    ]
    _legacy_runtime_database(path, messages=messages)

    # Migrations 13 (run facts) and 14 (participant join order) run after it;
    # the append-order checks below are migration 12's.
    assert migrate_database(path).applied_versions == (12, 13, 14)

    with closing(sqlite3.connect(str(path))) as conn:
        rows = conn.execute(
            "SELECT id, created_at, rowid, append_seq FROM messages ORDER BY rowid"
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (item[0], item[3]) for item in messages
    ]
    assert all(row[2] == row[3] for row in rows)
    with closing(connect_database(path)) as conn:
        store = SessionStore(conn)
        assert [item.body for item in store.list_messages(session_id="s1")] == [
            "s1-first",
            "s1-second",
            "s1-third",
            "s1-fourth",
        ]
        assert [item.body for item in store.list_messages(session_id="s2")] == [
            "s2-first",
            "s2-second",
        ]
        with patch(
            "openminion.modules.storage.runtime.session_store.messages.utc_now_iso",
            return_value=T1,
        ):
            appended = store.append_message(
                session_id="s2", role="outbound", body="s2-after-upgrade"
            )
        assert appended.rowid == 3
        assert [item.body for item in store.list_messages(session_id="s2")] == [
            "s2-first",
            "s2-second",
            "s2-after-upgrade",
        ]
        assert (
            conn.execute(
                "SELECT append_seq FROM messages WHERE id = ?", (appended.id,)
            ).fetchone()[0]
            == 6
        )


def test_append_seq_migration_moves_boundary_out_of_tied_anchor_group(
    tmp_path: Path,
) -> None:
    path = tmp_path / "openminion.db"
    # g1-g4 share T1 and their ids sort in reverse of append order. Compaction
    # ran after g3 and stored position 2 in the old (created_at, id) order:
    # e0, g3. g4 was appended later in the same clock tick.
    _legacy_runtime_database(
        path,
        messages=[
            ("5" * 32, "s", "e0", T0),
            ("f" * 32, "s", "g1", T1),
            ("c" * 32, "s", "g2", T1),
            ("8" * 32, "s", "g3", T1),
            ("0" * 32, "s", "g4", T1),
            ("4" * 32, "s", "l1", T2),
            ("9" * 32, "fresh", "fresh-1", T1),
        ],
        contexts=[
            ("s", "summary of e0, g3", 2, T1, "8" * 32, 2),
            ("fresh", "", 0, "", "", 0),
        ],
    )
    with closing(sqlite3.connect(str(path))) as conn:
        old_order = conn.execute(
            "SELECT body FROM messages WHERE session_id = 's' ORDER BY created_at, id"
        ).fetchall()
    # Before the upgrade position 2 had drifted: it hid the uncompacted g4 and
    # showed the compacted g3.
    assert [row[0] for row in old_order[:2]] == ["e0", "g4"]
    fresh_before = _context_row(path, "fresh")
    version_before, updated_before = _context_row(path, "s")[-2:]

    migrate_database(path)

    assert _context_row(path, "s") == (
        "s",
        "summary of e0, g3",
        1,
        T0,
        "5" * 32,
        1,
        version_before,
        updated_before,
    )
    assert _visible_bodies(path, "s") == ["g1", "g2", "g3", "g4", "l1"]
    assert _context_row(path, "fresh") == fresh_before


def test_append_seq_migration_keeps_boundary_when_anchor_has_own_timestamp(
    tmp_path: Path,
) -> None:
    path = tmp_path / "openminion.db"
    _legacy_runtime_database(
        path,
        messages=[
            ("f" * 32, "s", "before-1", T0),
            ("c" * 32, "s", "before-2", T0),
            ("8" * 32, "s", "anchor", T1),
            ("5" * 32, "s", "after-1", T2),
            ("0" * 32, "s", "after-2", T2),
        ],
        contexts=[("s", "summary", 3, T1, "8" * 32, 3)],
    )
    before = _context_row(path, "s")

    migrate_database(path)

    assert _context_row(path, "s") == before
    assert _visible_bodies(path, "s") == ["after-1", "after-2"]


def test_append_seq_migration_moves_boundary_before_missing_anchor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "openminion.db"
    _legacy_runtime_database(
        path,
        messages=[
            ("5" * 32, "s", "e0", T0),
            ("f" * 32, "s", "g1", T1),
        ],
        contexts=[("s", "summary", 2, T1, "9" * 32, 2)],
    )

    migrate_database(path)

    assert _context_row(path, "s")[:6] == ("s", "summary", 1, T0, "5" * 32, 1)
    assert _visible_bodies(path, "s") == ["g1"]


def test_join_seq_migration_numbers_participants_in_pre_upgrade_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "openminion.db"
    migrate_database(
        path,
        migrations=tuple(item for item in DEFAULT_MIGRATIONS if item.version < 14),
    )
    sessions, participants = legacy_participant_rows()
    with closing(sqlite3.connect(str(path))) as conn:
        conn.executemany(
            "INSERT INTO sessions(id, session_key, channel, target, active_agent_id) "
            "VALUES (:id, :session_key, :channel, :target, :active_agent_id)",
            sessions,
        )
        conn.executemany(
            "INSERT INTO room_participants(id, session_id, participant_type, "
            "participant_id, joined_at, left_at) VALUES (:id, :session_id, "
            ":participant_type, :participant_id, :joined_at, :left_at)",
            participants,
        )
        conn.commit()
        for session_id, order in LEGACY_ORDER.items():
            assert [
                row[0] for row in conn.execute(PRE_UPGRADE_ORDER_SQL, (session_id,))
            ] == order
        counted = {row[0]: row[1] for row in conn.execute(COUNTED_JOIN_SEQ_SQL)}

    assert migrate_database(path).applied_versions == (14,)

    with closing(connect_database(path)) as conn:
        assert [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'room_participants' AND sql LIKE 'CREATE UNIQUE%'"
            )
        ] == ["idx_room_participants_session_join_seq"]
        check_upgraded_join_order(SessionStore(conn), counted)
