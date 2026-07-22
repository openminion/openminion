import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from openminion.modules.storage.runtime.migrations import (
    Migration,
    MigrationError,
    migrate_database,
    run_migrations,
)
from openminion.modules.storage.runtime.sqlite import connect_database


class StorageMigrationTests(unittest.TestCase):
    def test_migrate_database_creates_expected_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "state" / "openminion.db"

            result = migrate_database(database_path)
            self.assertEqual(result.current_version, 10)
            self.assertEqual(
                result.applied_versions,
                (1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
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
                    ],
                )

    def test_migrate_database_is_idempotent_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "state" / "openminion.db"

            first = migrate_database(database_path)
            second = migrate_database(database_path)

            self.assertEqual(first.current_version, 10)
            self.assertEqual(
                first.applied_versions,
                (1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
            )
            self.assertEqual(second.current_version, 10)
            self.assertEqual(second.applied_versions, ())

            with sqlite3.connect(str(database_path)) as conn:
                migration_count = conn.execute(
                    "SELECT COUNT(*) FROM migrations"
                ).fetchone()[0]
                self.assertEqual(migration_count, 10)

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
