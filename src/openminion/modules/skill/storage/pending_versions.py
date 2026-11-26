"""Operator listing of staged skill versions that still await admission."""

from __future__ import annotations

import json
from typing import Any

from openminion.modules.storage.record_store import RecordStore


def skill_scope_clauses(
    *,
    scopes: list[str] | None,
    agent_id: str | None,
    repository_root: str | None = None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if scopes:
        placeholders = ",".join(["?"] * len(scopes))
        clauses.append(f"s.scope IN ({placeholders})")
        params.extend(scopes)

    visible = ["s.scope = 'global'"]
    if agent_id:
        visible.append("(s.scope = 'agent' AND (s.agent_id IS NULL OR s.agent_id = ?))")
        params.append(agent_id)
    if repository_root:
        visible.append("(s.scope = 'project' AND s.repository_root = ?)")
        params.append(repository_root)
    clauses.append("(" + " OR ".join(visible) + ")")
    return clauses, params


class PendingSkillVersionStoreMixin:
    _record_store: RecordStore

    def list_pending_skill_versions(
        self,
        *,
        scope: str | None = None,
        agent_id: str | None = None,
        repository_root: str | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses, params = skill_scope_clauses(
            scopes=[scope] if scope else None,
            agent_id=agent_id,
            repository_root=repository_root,
        )
        rows = self._record_store.query_dicts(
            f"""
            SELECT s.skill_id, s.name, s.scope, s.agent_id, s.repository_root,
                   sv.version_hash, sv.package_json, sv.created_at
            FROM skill_version_admissions a
            JOIN skills s ON s.skill_id = a.skill_id
            JOIN skill_versions sv
              ON sv.skill_id = a.skill_id AND sv.version_hash = a.version_hash
            WHERE a.state = 'pending' AND {" AND ".join(where_clauses)}
            ORDER BY s.skill_id ASC, sv.created_at DESC, sv.version_hash DESC
            """,
            params,
        )
        return [
            {
                "skill_id": str(row["skill_id"]),
                "name": str(row["name"]),
                "scope": str(row["scope"]),
                "agent_id": row["agent_id"],
                "repository_root": row["repository_root"],
                "version_hash": str(row["version_hash"]),
                "package": json.loads(str(row["package_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
