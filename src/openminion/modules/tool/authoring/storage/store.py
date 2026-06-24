"""SQLite storage for modules tool authoring."""

from pathlib import Path

from openminion.modules.storage import BaseModuleSQLiteStore

from .migrations import list_migrations
from ..schemas import AuthoredToolAuditEventRow, AuthoredToolRow, ToolDraftRow


class SQLiteAuthoredToolStore(BaseModuleSQLiteStore):
    """SQLite-backed store for authored-tool drafts, registry rows, and audit rows."""

    def __init__(self, sqlite_path: str | Path, *, wal: bool = True) -> None:
        super().__init__(sqlite_path=sqlite_path, wal=wal)

    def _init_schema(self) -> None:
        return None

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def insert_draft(self, row: ToolDraftRow) -> str:
        self._record_store.execute_count(
            """
            INSERT INTO tool_drafts (
                draft_id, local_name, description, source_code, unit_tests_source,
                args_schema_json, returns_schema_json, requirements_json,
                dependencies_json, proposed_scope_tier, status, inspect_result_json,
                created_at, created_by_agent_id, created_by_session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.draft_id,
                row.local_name,
                row.description,
                row.source_code,
                row.unit_tests_source,
                row.args_schema_json,
                row.returns_schema_json,
                row.requirements_json,
                row.dependencies_json,
                row.proposed_scope_tier,
                row.status,
                row.inspect_result_json,
                row.created_at,
                row.created_by_agent_id,
                row.created_by_session_id,
            ),
        )
        return row.draft_id

    def update_draft_inspection(
        self,
        draft_id: str,
        *,
        status: str,
        inspect_result_json: str,
    ) -> bool:
        updated = self._record_store.execute_count(
            """
            UPDATE tool_drafts
            SET status = ?, inspect_result_json = ?
            WHERE draft_id = ?
            """,
            (status, inspect_result_json, draft_id),
        )
        return bool(updated)

    def mark_draft_registered(self, draft_id: str) -> bool:
        updated = self._record_store.execute_count(
            """
            UPDATE tool_drafts
            SET status = 'registered'
            WHERE draft_id = ?
            """,
            (draft_id,),
        )
        return bool(updated)

    def get_draft(self, draft_id: str) -> ToolDraftRow | None:
        rows = self._record_store.query_dicts(
            """
            SELECT draft_id, local_name, description, source_code, unit_tests_source,
                   args_schema_json, returns_schema_json, requirements_json,
                   dependencies_json, proposed_scope_tier, status, inspect_result_json,
                   created_at, created_by_agent_id, created_by_session_id
            FROM tool_drafts
            WHERE draft_id = ?
            """,
            (draft_id,),
        )
        if not rows:
            return None
        return ToolDraftRow(**rows[0])

    def insert_authored_tool(self, row: AuthoredToolRow) -> str:
        self._record_store.execute_count(
            """
            INSERT INTO authored_tools (
                tool_name, local_name, version_number, version_hash, source_code,
                unit_tests_source, args_schema_json, returns_schema_json, description,
                dependencies_json, tier, min_scope, policy_grant_id, created_at,
                updated_at, created_by_agent_id, promoted_at, promoted_by,
                success_count, failure_count, last_invocation_at, removed_at, removed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.tool_name,
                row.local_name,
                row.version_number,
                row.version_hash,
                row.source_code,
                row.unit_tests_source,
                row.args_schema_json,
                row.returns_schema_json,
                row.description,
                row.dependencies_json,
                row.tier,
                row.min_scope,
                row.policy_grant_id,
                row.created_at,
                row.updated_at,
                row.created_by_agent_id,
                row.promoted_at,
                row.promoted_by,
                row.success_count,
                row.failure_count,
                row.last_invocation_at,
                row.removed_at,
                row.removed_by,
            ),
        )
        return row.tool_name

    def update_authored_invocation(
        self,
        tool_name: str,
        *,
        ok: bool,
        invoked_at: str,
    ) -> bool:
        field_name = "success_count" if ok else "failure_count"
        updated = self._record_store.execute_count(
            f"""
            UPDATE authored_tools
            SET {field_name} = {field_name} + 1,
                last_invocation_at = ?,
                updated_at = ?
            WHERE tool_name = ?
            """,
            (invoked_at, invoked_at, tool_name),
        )
        return bool(updated)

    def update_authored_promotion(
        self,
        tool_name: str,
        *,
        tier: str,
        promoted_at: str,
        promoted_by: str | None,
    ) -> bool:
        updated = self._record_store.execute_count(
            """
            UPDATE authored_tools
            SET tier = ?, promoted_at = ?, promoted_by = ?, updated_at = ?
            WHERE tool_name = ?
            """,
            (tier, promoted_at, promoted_by, promoted_at, tool_name),
        )
        return bool(updated)

    def update_authored_scope(
        self,
        tool_name: str,
        *,
        scope: str,
        updated_at: str,
    ) -> bool:
        updated = self._record_store.execute_count(
            """
            UPDATE authored_tools
            SET min_scope = ?, updated_at = ?
            WHERE tool_name = ?
            """,
            (scope, updated_at, tool_name),
        )
        return bool(updated)

    def attach_policy_grant(
        self,
        tool_name: str,
        *,
        grant_id: str | None,
        updated_at: str,
    ) -> bool:
        updated = self._record_store.execute_count(
            """
            UPDATE authored_tools
            SET policy_grant_id = ?, updated_at = ?
            WHERE tool_name = ?
            """,
            (grant_id, updated_at, tool_name),
        )
        return bool(updated)

    def mark_tool_removed(
        self,
        tool_name: str,
        *,
        removed_at: str,
        removed_by: str | None,
    ) -> bool:
        updated = self._record_store.execute_count(
            """
            UPDATE authored_tools
            SET removed_at = ?, removed_by = ?, updated_at = ?
            WHERE tool_name = ?
            """,
            (removed_at, removed_by, removed_at, tool_name),
        )
        return bool(updated)

    def get_authored_tool(self, tool_name: str) -> AuthoredToolRow | None:
        rows = self._record_store.query_dicts(
            """
            SELECT tool_name, local_name, version_number, version_hash, source_code,
                   unit_tests_source, args_schema_json, returns_schema_json, description,
                   dependencies_json, tier, min_scope, policy_grant_id, created_at,
                   updated_at, created_by_agent_id, promoted_at, promoted_by,
                   success_count, failure_count, last_invocation_at, removed_at, removed_by
            FROM authored_tools
            WHERE tool_name = ?
            """,
            (tool_name,),
        )
        if not rows:
            return None
        return AuthoredToolRow(**rows[0])

    def get_authored_tool_by_name_hash(
        self,
        local_name: str,
        version_hash: str,
    ) -> AuthoredToolRow | None:
        rows = self._record_store.query_dicts(
            """
            SELECT tool_name, local_name, version_number, version_hash, source_code,
                   unit_tests_source, args_schema_json, returns_schema_json, description,
                   dependencies_json, tier, min_scope, policy_grant_id, created_at,
                   updated_at, created_by_agent_id, promoted_at, promoted_by,
                   success_count, failure_count, last_invocation_at, removed_at, removed_by
            FROM authored_tools
            WHERE local_name = ? AND version_hash = ?
            """,
            (local_name, version_hash),
        )
        if not rows:
            return None
        return AuthoredToolRow(**rows[0])

    def next_version_number(self, local_name: str) -> int:
        rows = self._record_store.query_dicts(
            """
            SELECT COALESCE(MAX(version_number), 0) AS max_version
            FROM authored_tools
            WHERE local_name = ?
            """,
            (local_name,),
        )
        current = int(rows[0]["max_version"]) if rows else 0
        return current + 1

    def list_authored_tools(
        self,
        *,
        tier: str | None = None,
        include_removed: bool = False,
    ) -> list[AuthoredToolRow]:
        sql = """
            SELECT tool_name, local_name, version_number, version_hash, source_code,
                   unit_tests_source, args_schema_json, returns_schema_json, description,
                   dependencies_json, tier, min_scope, policy_grant_id, created_at,
                   updated_at, created_by_agent_id, promoted_at, promoted_by,
                   success_count, failure_count, last_invocation_at, removed_at, removed_by
            FROM authored_tools
        """
        params: list[object] = []
        clauses: list[str] = []
        if tier and str(tier).strip().lower() != "all":
            clauses.append("tier = ?")
            params.append(tier)
        if not include_removed:
            clauses.append("removed_at IS NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY local_name ASC, version_number ASC"
        rows = self._record_store.query_dicts(sql, tuple(params))
        return [AuthoredToolRow(**row) for row in rows]

    def list_registered(self) -> list[AuthoredToolRow]:
        return self.list_authored_tools(include_removed=False)

    def insert_audit_event(self, row: AuthoredToolAuditEventRow) -> str:
        self._record_store.execute_count(
            """
            INSERT INTO tool_authoring_audit_events (
                event_id, timestamp, event_type, target_kind, target_id,
                agent_id, session_id, version_hash, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.event_id,
                row.timestamp,
                row.event_type,
                row.target_kind,
                row.target_id,
                row.agent_id,
                row.session_id,
                row.version_hash,
                row.details_json,
            ),
        )
        return row.event_id

    def list_audit_events(
        self,
        *,
        target_kind: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthoredToolAuditEventRow]:
        sql = """
            SELECT event_id, timestamp, event_type, target_kind, target_id,
                   agent_id, session_id, version_hash, details_json
            FROM tool_authoring_audit_events
        """
        clauses: list[str] = []
        params: list[object] = []
        if target_kind:
            clauses.append("target_kind = ?")
            params.append(target_kind)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, event_id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = self._record_store.query_dicts(sql, tuple(params))
        return [AuthoredToolAuditEventRow(**row) for row in rows]
