from __future__ import annotations

from openminion.tools.mcp.compatibility import (
    default_mcp_compatibility_matrix,
    default_mcp_security_fuzz_cases,
    validate_mcp_compatibility_matrix,
)
from openminion.tools.mcp.server import PublishedTool, handle_published_mcp_request


def test_mcp_compatibility_matrix_covers_required_server_families() -> None:
    matrix = default_mcp_compatibility_matrix()
    assert validate_mcp_compatibility_matrix() == []
    assert {case.family for case in matrix} == {
        "filesystem",
        "git/github",
        "browser",
        "database",
        "docs/search",
        "everything",
    }
    assert all(case.ci_safe_fixture for case in matrix)


def test_mcp_security_fuzz_matrix_covers_hostile_fixture_classes() -> None:
    cases = default_mcp_security_fuzz_cases()
    assert {case.case_id for case in cases} == {
        "mcp-fuzz-malformed-frame",
        "mcp-fuzz-bad-schema",
        "mcp-fuzz-auth-challenge",
        "mcp-fuzz-hostile-content",
    }
    assert all(case.expected_result for case in cases)


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
            "params": {"name": "safe", "arguments": "not-an-object"},
        },
    )

    assert response is not None
    assert response["error"]["code"] == -32602
    assert "arguments" in response["error"]["message"]


def test_published_jsonrpc_fuzz_unknown_method_fails_closed() -> None:
    response = handle_published_mcp_request(
        [],
        {"jsonrpc": "2.0", "id": 11, "method": "tools/deleteEverything"},
    )

    assert response is not None
    assert response["error"]["code"] == -32601
    assert "unsupported MCP server method" in response["error"]["message"]
