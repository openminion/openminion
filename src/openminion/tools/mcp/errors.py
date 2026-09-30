"""MCP transport failures shared by stdio and HTTP."""

from typing import Any


class MCPTransportError(RuntimeError):
    """Base transport error for MCP lifecycle failures."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip()
        self.details = dict(details or {})


class MCPServerUnavailableError(MCPTransportError):
    """Raised when the MCP server process or endpoint is unavailable."""


class MCPTimeoutError(MCPTransportError):
    """Raised when the MCP server does not reply before the deadline."""


class MCPProtocolError(MCPTransportError):
    """Raised when the MCP server returns malformed protocol data."""


class MCPRemoteTransportError(MCPTransportError):
    """Raised when remote MCP transport fails structurally."""
