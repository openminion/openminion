from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .schemas import AuthoredToolRow, ToolDraftRow


class ToolAuthoringServiceInterface(Protocol):
    """Public service boundary for authored-tool lifecycle operations."""

    def author_draft(
        self,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    def inspect_draft(
        self,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    def register_draft(
        self,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    def list_authored_tools(
        self,
        *,
        tier: str = "all",
        include_removed: bool = False,
    ) -> list[dict[str, Any]]: ...

    def get_authored_tool_detail(self, tool_name: str) -> dict[str, Any] | None: ...

    def promote_tool(
        self,
        tool_name: str,
        *,
        force: bool = False,
        actor_id: str | None = None,
    ) -> dict[str, Any]: ...

    def set_tool_scope(
        self,
        tool_name: str,
        *,
        scope: str,
        actor_id: str | None = None,
    ) -> dict[str, Any]: ...

    def remove_tool(
        self,
        tool_name: str,
        *,
        actor_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]: ...

    def register_runtime_tools(self, registry: Any) -> list[str]: ...

    def get_draft(self, draft_id: str) -> "ToolDraftRow | None": ...

    def get_authored_tool(self, tool_name: str) -> "AuthoredToolRow | None": ...
