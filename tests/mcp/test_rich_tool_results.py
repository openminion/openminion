from __future__ import annotations

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.manager import MCPProtocolError, MCPServerSession


class _ToolListTransport:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict]] = []

    def is_running(self) -> bool:
        return True

    def start(self) -> None:
        return None

    def notify(self, method: str, params: dict | None = None) -> None:
        self.notifications.append((method, dict(params or {})))

    def request(
        self,
        *,
        method: str,
        params: dict | None = None,
        timeout_seconds: float,
        server_request_handler=None,
    ) -> dict:
        del params, timeout_seconds, server_request_handler
        if method == "initialize":
            return {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "rich-tool",
                        "description": "rich result fixture",
                        "inputSchema": {"type": "object", "properties": {}},
                        "outputSchema": {
                            "type": "object",
                            "properties": {"status": {"type": "string"}},
                            "required": ["status"],
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        return {}


def _session() -> MCPServerSession:
    return MCPServerSession(
        MCPServerConfig(name="Fixture", command=["python", "-m", "fixture"])
    )


def test_mcp_tool_discovery_preserves_output_schema() -> None:
    session = _session()
    session._transport = _ToolListTransport()  # noqa: SLF001

    tools = session.list_tools()

    assert tools[0].output_schema == {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }


def test_mcp_tool_result_preserves_rich_content_and_validates_structured_content() -> (
    None
):
    session = _session()
    session._output_schemas_by_tool["rich-tool"] = {  # noqa: SLF001
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }

    normalized = session._normalize_call_result(  # noqa: SLF001
        remote_name="rich-tool",
        result={
            "content": [
                {"type": "text", "text": "done"},
                {"type": "image", "mimeType": "image/png", "data": "abc123"},
                {"type": "audio", "mimeType": "audio/wav", "data": "def456"},
                {"type": "resource_link", "uri": "file://fixture/readme.md"},
            ],
            "structuredContent": {"status": "ok"},
            "isError": False,
        },
    )

    assert normalized["content"] == "done"
    assert normalized["data"]["structured_content"] == {"status": "ok"}
    assert normalized["data"]["output_schema"]["required"] == ["status"]
    content_items = normalized["data"]["content_items"]
    assert [item["type"] for item in content_items] == [
        "text",
        "image",
        "audio",
        "resource_link",
    ]
    assert content_items[1]["mimeType"] == "image/png"
    assert content_items[2]["mimeType"] == "audio/wav"
    assert content_items[3]["uri"] == "file://fixture/readme.md"


def test_mcp_tool_result_rejects_invalid_structured_content() -> None:
    session = _session()
    session._output_schemas_by_tool["rich-tool"] = {  # noqa: SLF001
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }

    with pytest.raises(MCPProtocolError) as excinfo:
        session._normalize_call_result(  # noqa: SLF001
            remote_name="rich-tool",
            result={
                "content": [{"type": "text", "text": "bad"}],
                "structuredContent": {"status": 123},
                "isError": False,
            },
        )

    assert excinfo.value.reason_code == "mcp_output_schema_invalid"
