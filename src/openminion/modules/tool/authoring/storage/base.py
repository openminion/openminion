"""Persistence boundary for modules tool authoring storage."""

from typing import Protocol, runtime_checkable

from ..schemas import AuthoredToolAuditEventRow, AuthoredToolRow, ToolDraftRow


@runtime_checkable
class AuthoredToolStore(Protocol):
    """Protocol describing the authored-tool persistence boundary."""

    def insert_draft(self, row: ToolDraftRow) -> str: ...

    def update_draft_inspection(
        self,
        draft_id: str,
        *,
        status: str,
        inspect_result_json: str,
    ) -> bool: ...

    def mark_draft_registered(self, draft_id: str) -> bool: ...

    def get_draft(self, draft_id: str) -> ToolDraftRow | None: ...

    def insert_authored_tool(self, row: AuthoredToolRow) -> str: ...

    def update_authored_invocation(
        self,
        tool_name: str,
        *,
        ok: bool,
        invoked_at: str,
    ) -> bool: ...

    def update_authored_promotion(
        self,
        tool_name: str,
        *,
        tier: str,
        promoted_at: str,
        promoted_by: str | None,
    ) -> bool: ...

    def update_authored_scope(
        self,
        tool_name: str,
        *,
        scope: str,
        updated_at: str,
    ) -> bool: ...

    def attach_policy_grant(
        self,
        tool_name: str,
        *,
        grant_id: str | None,
        updated_at: str,
    ) -> bool: ...

    def mark_tool_removed(
        self,
        tool_name: str,
        *,
        removed_at: str,
        removed_by: str | None,
    ) -> bool: ...

    def get_authored_tool(self, tool_name: str) -> AuthoredToolRow | None: ...

    def get_authored_tool_by_name_hash(
        self,
        local_name: str,
        version_hash: str,
    ) -> AuthoredToolRow | None: ...

    def next_version_number(self, local_name: str) -> int: ...

    def list_authored_tools(
        self,
        *,
        tier: str | None = None,
        include_removed: bool = False,
    ) -> list[AuthoredToolRow]: ...

    def list_registered(self) -> list[AuthoredToolRow]: ...

    def insert_audit_event(self, row: AuthoredToolAuditEventRow) -> str: ...

    def list_audit_events(
        self,
        *,
        target_kind: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthoredToolAuditEventRow]: ...

    def close(self) -> None: ...
