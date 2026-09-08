from __future__ import annotations

import sqlite3

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def _bootstrap_legacy_schema(db_path, session_id: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE sessions (
              session_id           TEXT PRIMARY KEY,
              created_at           TEXT NOT NULL,
              updated_at           TEXT NOT NULL,
              title                TEXT,
              status               TEXT NOT NULL,
              active_agent_id      TEXT,
              participants_json    TEXT NOT NULL DEFAULT '[]',
              root_goal            TEXT,
              tags_json            TEXT NOT NULL DEFAULT '[]',
              config_snapshot_ref  TEXT,
              meta_json            TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE turns (
              turn_id           TEXT PRIMARY KEY,
              session_id        TEXT NOT NULL,
              ts                TEXT NOT NULL,
              role              TEXT NOT NULL,
              content           TEXT NOT NULL,
              attachments_json  TEXT NOT NULL DEFAULT '[]',
              meta_json         TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE events (
              event_id            TEXT PRIMARY KEY,
              session_id          TEXT NOT NULL,
              ts                  TEXT NOT NULL,
              type                TEXT NOT NULL,
              agent_id            TEXT,
              trace_id            TEXT,
              task_id             TEXT,
              parent_id           TEXT,
              payload_json        TEXT NOT NULL DEFAULT '{}',
              artifact_refs_json  TEXT NOT NULL DEFAULT '[]',
              memory_refs_json    TEXT NOT NULL DEFAULT '[]',
              status              TEXT,
              error_json          TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sessions(session_id, created_at, updated_at, title, status, active_agent_id)
            VALUES (?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'legacy session', 'active', NULL)
            """,
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()


def test_existing_v1_schema_is_migrated_transparently(tmp_path) -> None:
    session_id = "legacy-session"
    db_path = tmp_path / "legacy.db"
    _bootstrap_legacy_schema(db_path, session_id)

    store = SQLiteSessionStore(db_path)
    try:
        session = store.get_session(session_id)
        assert session is not None
        assert session["title"] == "legacy session"
        assert session.get("active_profile_version") is None

        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        assert "active_profile_version" in columns

        applied_migrations = {
            row["version"]
            for row in store._conn.execute("SELECT version FROM migrations").fetchall()
        }
        assert {1, 2}.issubset(applied_migrations)

        store.bind_agent(session_id, agent_id="agent.beta", profile_version="pv2")
        updated = store.get_session(session_id)
        assert updated is not None
        assert updated["active_agent_id"] == "agent.beta"
        assert updated["active_profile_version"] == "pv2"

        turn_id = store.append_turn(session_id, role="user", content="hi from legacy")
        assert turn_id

        events = store.get_events(session_id, limit=10)
        assert len(events) >= 1
    finally:
        store.close()
