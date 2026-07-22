from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from openminion.base.time import utc_now_iso as _now_iso
from .sqlite import connect_database


@dataclass(frozen=True)
class AgentRegistryRecord:
    agent_id: str
    display_name: str
    description: str
    config_path: str
    workspace_root: str
    tags: list[str]
    status: str  # registered | stopped
    registered_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentHeartbeatRecord:
    agent_id: str
    pid: int
    host: str
    port: int
    status: str  # idle | running | error | stopped
    active_run_id: str
    last_heartbeat_at: str
    started_at: str
    metadata: dict[str, Any]


class AgentRegistryStore:
    """Persistent registry of known agents and their runtime heartbeat state."""

    def __init__(self, database_path: str) -> None:
        self._path = database_path

    # Registry (static agent definitions)

    def upsert_agent(
        self,
        *,
        agent_id: str,
        display_name: str = "",
        description: str = "",
        config_path: str = "",
        workspace_root: str = "",
        tags: list[str] | None = None,
        status: str = "registered",
    ) -> None:
        tags_json = json.dumps(tags or [])
        now = _now_iso()
        with connect_database(self._path) as conn:
            conn.execute(
                """
                INSERT INTO daemon_registry
                    (agent_id, display_name, description, config_path, workspace_root,
                     tags_json, status, registered_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    description = excluded.description,
                    config_path = excluded.config_path,
                    workspace_root = excluded.workspace_root,
                    tags_json = excluded.tags_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    agent_id,
                    display_name,
                    description,
                    config_path,
                    workspace_root,
                    tags_json,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()

    def set_agent_status(self, *, agent_id: str, status: str) -> None:
        with connect_database(self._path) as conn:
            conn.execute(
                "UPDATE daemon_registry SET status = ?, updated_at = ? WHERE agent_id = ?",
                (status, _now_iso(), agent_id),
            )
            conn.commit()

    def get_agent(self, agent_id: str) -> Optional[AgentRegistryRecord]:
        with connect_database(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM daemon_registry WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        if row is None:
            return None
        return _row_to_registry(row)

    def list_agents(self, *, status: Optional[str] = None) -> list[AgentRegistryRecord]:
        if status:
            sql = "SELECT * FROM daemon_registry WHERE status = ? ORDER BY registered_at ASC"
            params: tuple = (status,)
        else:
            sql = "SELECT * FROM daemon_registry ORDER BY registered_at ASC"
            params = ()
        with connect_database(self._path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_registry(row) for row in rows]

    # Heartbeats (live runtime state)

    def upsert_heartbeat(
        self,
        *,
        agent_id: str,
        pid: int = 0,
        host: str = "",
        port: int = 0,
        status: str = "idle",
        active_run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        metadata_json = json.dumps(metadata or {})
        with connect_database(self._path) as conn:
            conn.execute(
                """
                INSERT INTO daemon_heartbeats
                    (agent_id, pid, host, port, status, active_run_id,
                     last_heartbeat_at, started_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    pid = excluded.pid,
                    host = excluded.host,
                    port = excluded.port,
                    status = excluded.status,
                    active_run_id = excluded.active_run_id,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    agent_id,
                    pid,
                    host,
                    port,
                    status,
                    active_run_id,
                    now,
                    now,
                    metadata_json,
                ),
            )
            conn.commit()

    def get_heartbeat(self, agent_id: str) -> Optional[AgentHeartbeatRecord]:
        with connect_database(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM daemon_heartbeats WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        if row is None:
            return None
        return _row_to_heartbeat(row)

    def list_heartbeats(self) -> list[AgentHeartbeatRecord]:
        with connect_database(self._path) as conn:
            rows = conn.execute(
                "SELECT * FROM daemon_heartbeats ORDER BY last_heartbeat_at DESC"
            ).fetchall()
        return [_row_to_heartbeat(row) for row in rows]

    def remove_heartbeat(self, agent_id: str) -> None:
        with connect_database(self._path) as conn:
            conn.execute(
                "DELETE FROM daemon_heartbeats WHERE agent_id = ?", (agent_id,)
            )
            conn.commit()

    def is_agent_stale(self, agent_id: str, *, stale_seconds: int = 60) -> bool:
        """Return True if the heartbeat last_heartbeat_at is older than stale_seconds."""
        hb = self.get_heartbeat(agent_id)
        if hb is None:
            return True
        try:
            last = datetime.fromisoformat(hb.last_heartbeat_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            delta = (datetime.now(timezone.utc) - last).total_seconds()
            return delta > stale_seconds
        except (ValueError, TypeError):
            return True


def _row_to_registry(row: Any) -> AgentRegistryRecord:
    if hasattr(row, "keys"):
        d = dict(row)
    else:
        d = {
            "agent_id": row[0],
            "display_name": row[1],
            "description": row[2],
            "config_path": row[3],
            "workspace_root": row[4],
            "tags_json": row[5],
            "status": row[6],
            "registered_at": row[7],
            "updated_at": row[8],
        }
    try:
        tags = json.loads(d.get("tags_json") or "[]")
        if not isinstance(tags, list):
            tags = []
    except (json.JSONDecodeError, TypeError):
        tags = []
    return AgentRegistryRecord(
        agent_id=str(d.get("agent_id", "")),
        display_name=str(d.get("display_name", "")),
        description=str(d.get("description", "")),
        config_path=str(d.get("config_path", "")),
        workspace_root=str(d.get("workspace_root", "")),
        tags=tags,
        status=str(d.get("status", "")),
        registered_at=str(d.get("registered_at", "")),
        updated_at=str(d.get("updated_at", "")),
    )


def _row_to_heartbeat(row: Any) -> AgentHeartbeatRecord:
    if hasattr(row, "keys"):
        d = dict(row)
    else:
        d = {
            "agent_id": row[0],
            "pid": row[1],
            "host": row[2],
            "port": row[3],
            "status": row[4],
            "active_run_id": row[5],
            "last_heartbeat_at": row[6],
            "started_at": row[7],
            "metadata_json": row[8],
        }
    try:
        metadata = json.loads(d.get("metadata_json") or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    return AgentHeartbeatRecord(
        agent_id=str(d.get("agent_id", "")),
        pid=int(d.get("pid", 0)),
        host=str(d.get("host", "")),
        port=int(d.get("port", 0)),
        status=str(d.get("status", "idle")),
        active_run_id=str(d.get("active_run_id", "")),
        last_heartbeat_at=str(d.get("last_heartbeat_at", "")),
        started_at=str(d.get("started_at", "")),
        metadata=metadata,
    )
