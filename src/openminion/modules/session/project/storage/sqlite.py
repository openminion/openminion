"""SQLite project storage."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from openminion.modules.session.project.schemas import (
    Project,
    ProjectSessionBinding,
)
from openminion.modules.session.project.storage.base import ProjectStore


_DDL_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    master_instruction TEXT NOT NULL DEFAULT '',
    skill_set_json TEXT NOT NULL DEFAULT '[]',
    scheduled_triggers_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
)
"""

_DDL_BINDINGS = """
CREATE TABLE IF NOT EXISTS project_session_bindings (
    binding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE,
    bound_at TEXT NOT NULL
)
"""

_DDL_BINDINGS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_psb_project ON project_session_bindings(project_id)"
)


class SQLiteProjectStore(ProjectStore):
    def __init__(self, sqlite_path: str | Path) -> None:
        self._path = str(sqlite_path)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(_DDL_PROJECTS)
        self._conn.execute(_DDL_BINDINGS)
        self._conn.execute(_DDL_BINDINGS_IDX)
        self._conn.commit()

    def create(self, project: Project) -> Project:
        self._conn.execute(
            "INSERT INTO projects(project_id, name, master_instruction, "
            "skill_set_json, scheduled_triggers_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                project.project_id,
                project.name,
                project.master_instruction,
                json.dumps(list(project.skill_set)),
                json.dumps(list(project.scheduled_triggers)),
                project.created_at,
            ),
        )
        self._conn.commit()
        return project

    def get(self, project_id: str) -> Project | None:
        cur = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_project(row)

    def list(self) -> list[Project]:
        cur = self._conn.execute("SELECT * FROM projects ORDER BY created_at ASC")
        return [_row_to_project(r) for r in cur.fetchall()]

    def delete(self, project_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM projects WHERE project_id = ?", (project_id,)
        )
        self._conn.execute(
            "DELETE FROM project_session_bindings WHERE project_id = ?",
            (project_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def bind_session(self, project_id: str, session_id: str) -> ProjectSessionBinding:
        binding = ProjectSessionBinding(project_id=project_id, session_id=session_id)
        self._conn.execute(
            "INSERT OR REPLACE INTO project_session_bindings"
            "(project_id, session_id, bound_at) VALUES (?, ?, ?)",
            (binding.project_id, binding.session_id, binding.bound_at),
        )
        self._conn.commit()
        return binding

    def list_bindings_for_project(self, project_id: str) -> list[ProjectSessionBinding]:
        cur = self._conn.execute(
            "SELECT project_id, session_id, bound_at FROM project_session_bindings"
            " WHERE project_id = ? ORDER BY bound_at ASC",
            (project_id,),
        )
        return [
            ProjectSessionBinding(
                project_id=r["project_id"],
                session_id=r["session_id"],
                bound_at=r["bound_at"],
            )
            for r in cur.fetchall()
        ]

    def project_for_session(self, session_id: str) -> Project | None:
        cur = self._conn.execute(
            "SELECT project_id FROM project_session_bindings WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self.get(row["project_id"])


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        project_id=row["project_id"],
        name=row["name"],
        master_instruction=row["master_instruction"] or "",
        skill_set=json.loads(row["skill_set_json"] or "[]"),
        scheduled_triggers=json.loads(row["scheduled_triggers_json"] or "[]"),
        created_at=row["created_at"],
    )


__all__ = ["SQLiteProjectStore"]
