from __future__ import annotations

from openminion.tools.mcp.server import PublishedTool, handle_published_mcp_request


def _modern_meta() -> dict:
    return {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }


def test_published_jsonrpc_fuzz_bad_tool_call_params_returns_protocol_error() -> None:
    response = handle_published_mcp_request(
        [
            PublishedTool(
                name="safe",
                description="safe",
                input_schema={"type": "object"},
                handler=lambda _args: "ok",
            )
        ],
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                **_modern_meta(),
                "name": "safe",
                "arguments": "not-an-object",
            },
        },
    )

    assert response is not None
    assert response["error"]["code"] == -32602
    assert "arguments" in response["error"]["message"]


def test_published_jsonrpc_fuzz_unknown_method_fails_closed() -> None:
    response = handle_published_mcp_request(
        [],
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/deleteEverything",
            "params": _modern_meta(),
        },
    )

    assert response is not None
    assert response["error"]["code"] == -32601
    assert "unsupported MCP server method" in response["error"]["message"]
