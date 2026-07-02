"""SQLite state store for Slack wire-level adapter state."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION


class SlackStateStore:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self, sqlite_path: str | Path) -> None:
        self.path = Path(sqlite_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def mark_event_seen(self, event_id: str) -> bool:
        value = str(event_id or "").strip()
        if not value:
            return False
        try:
            self._conn.execute(
                "INSERT INTO slack_seen_events(event_id, first_seen_ts) VALUES (?, ?)",
                (value, time.time()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            return False
        return True

    def upsert_install(
        self,
        *,
        team_id: str,
        bot_user_id: str | None = None,
        bot_token_ref: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO slack_workspace_installs(
                team_id, bot_user_id, bot_token_ref, updated_at_ts
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                bot_user_id = excluded.bot_user_id,
                bot_token_ref = excluded.bot_token_ref,
                updated_at_ts = excluded.updated_at_ts
            """,
            (team_id, bot_user_id, bot_token_ref, time.time()),
        )
        self._conn.commit()

    def get_install(self, team_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM slack_workspace_installs WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS slack_seen_events (
                event_id TEXT PRIMARY KEY,
                first_seen_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slack_workspace_installs (
                team_id TEXT PRIMARY KEY,
                bot_user_id TEXT,
                bot_token_ref TEXT,
                updated_at_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slack_socket_diagnostics (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at_ts REAL NOT NULL
            );
            """
        )
        self._conn.commit()
