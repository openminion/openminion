"""Normalize MCP prompt, resource, and tool results."""

import json
from typing import Any

from .schemas import MCPArgumentValidationError, validate_mcp_arguments
from .transport import MCPProtocolError


class MCPManagerError(RuntimeError):
    """Base MCP manager error."""


class MCPCallError(MCPManagerError):
    """Raised when an MCP tool call returns an error envelope."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "mcp_upstream_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code.strip()
        self.details = dict(details or {})


def normalize_tool_result(
    *,
    server_name: str,
    remote_name: str,
    result: dict[str, Any],
    output_schema: dict[str, Any],
    stderr_tail: str,
) -> dict[str, Any]:
    content_items = result.get("content", [])
    normalized_content = [
        dict(item) for item in content_items if isinstance(item, dict)
    ]
    text_parts = [
        str(item.get("text", "") or "").strip()
        for item in normalized_content
        if str(item.get("type", "") or "").strip().lower() == "text"
        and str(item.get("text", "") or "").strip()
    ]
    structured_content = result.get("structuredContent")
    if output_schema and structured_content is not None:
        if not isinstance(structured_content, dict):
            raise MCPProtocolError(
                f"MCP tool '{server_name}.{remote_name}' returned non-object structuredContent.",
                reason_code="mcp_output_schema_invalid",
            )
        try:
            structured_content = validate_mcp_arguments(
                schema=output_schema,
                arguments=structured_content,
            )
        except MCPArgumentValidationError as exc:
            raise MCPProtocolError(
                f"MCP tool '{server_name}.{remote_name}' returned invalid structuredContent.",
                reason_code="mcp_output_schema_invalid",
            ) from exc
    content_text = "\n".join(text_parts).strip()
    if not content_text and structured_content is not None:
        content_text = json.dumps(structured_content, sort_keys=True)

    if result.get("isError"):
        details = {"mcp_stderr_tail": stderr_tail} if stderr_tail else {}
        cancelled = isinstance(structured_content, dict) and bool(
            structured_content.get("cancelled")
        )
        raise MCPCallError(
            content_text
            or f"MCP tool '{server_name}.{remote_name}' returned an error.",
            reason_code="mcp_client_cancelled" if cancelled else "mcp_upstream_error",
            details=details,
        )

    return {
        "ok": True,
        "verified": True,
        "content": content_text,
        "source": "mcp",
        "data": {
            "mcp_server": server_name,
            "mcp_remote_tool_name": remote_name,
            "content_items": normalized_content,
            "structured_content": structured_content,
            "output_schema": output_schema,
        },
    }


def normalize_prompt_result(
    *, server_name: str, remote_name: str, result: dict[str, Any]
) -> dict[str, Any]:
    raw_messages = result.get("messages", [])
    normalized_messages = [
        dict(item) for item in raw_messages if isinstance(item, dict)
    ]
    text_parts = [
        text
        for item in normalized_messages
        for text in _collect_text_fragments(item.get("content"))
    ]
    description = str(result.get("description", "") or "").strip()
    return {
        "ok": True,
        "verified": True,
        "content": "\n".join(text_parts).strip() or description,
        "source": "mcp",
        "data": {
            "mcp_server": server_name,
            "mcp_remote_prompt_name": remote_name,
            "messages": normalized_messages,
            "description": description,
        },
    }


def normalize_resource_result(
    *, server_name: str, resource_uri: str, result: dict[str, Any]
) -> dict[str, Any]:
    raw_contents = result.get("contents", [])
    normalized_contents = [
        dict(item) for item in raw_contents if isinstance(item, dict)
    ]
    text_parts = [
        str(item.get("text", "") or "").strip()
        for item in normalized_contents
        if str(item.get("text", "") or "").strip()
    ]
    return {
        "ok": True,
        "verified": True,
        "content": "\n".join(text_parts).strip(),
        "source": "mcp",
        "data": {
            "mcp_server": server_name,
            "mcp_resource_uri": resource_uri,
            "contents": normalized_contents,
        },
    }


def _collect_text_fragments(content: Any) -> list[str]:
    if isinstance(content, str):
        text = content.strip()
        return [text] if text else []
    if isinstance(content, dict):
        text = str(content.get("text", "") or "").strip()
        if text:
            return [text]
        return [
            part
            for value in content.values()
            for part in _collect_text_fragments(value)
        ]
    if isinstance(content, list):
        return [part for item in content for part in _collect_text_fragments(item)]
    return []


__all__ = [
    "MCPCallError",
    "MCPManagerError",
    "normalize_prompt_result",
    "normalize_resource_result",
    "normalize_tool_result",
]
