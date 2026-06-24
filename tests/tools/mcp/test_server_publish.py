from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.tools.mcp.server import (
    MCPServerError,
    PublishedTool,
    build_default_published_tools,
    build_runtime_published_tools,
    handle_published_mcp_request,
    invoke_published_tool,
    render_tools_list_payload,
)


def test_default_catalog_has_expected_minimum_four_families() -> None:
    tools = build_default_published_tools()
    names = {t.name for t in tools}
    assert "openminion.memory.export" in names
    assert "openminion.plan.show" in names
    assert "openminion.todo.list" in names
    assert "openminion.search.web" in names
    assert "openminion.fetch.url" in names
    assert len(tools) >= 4


def test_render_tools_list_payload_matches_mcp_shape() -> None:
    tools = build_default_published_tools()
    payload = render_tools_list_payload(tools)
    assert set(payload.keys()) == {"tools"}
    entry0 = payload["tools"][0]
    assert "name" in entry0
    assert "description" in entry0
    assert "inputSchema" in entry0
    # Round-trip through JSON to verify wire-shape stability.
    text = json.dumps(payload)
    re_parsed = json.loads(text)
    assert re_parsed == payload


def test_invoke_dispatches_to_matching_tool() -> None:
    captured: dict = {}

    def handler(args):
        captured.update(args)
        return {"echo": args}

    custom = PublishedTool(
        name="custom",
        description="t",
        input_schema={"type": "object", "additionalProperties": True},
        handler=handler,
    )
    result = invoke_published_tool([custom], name="custom", arguments={"x": 1})
    assert captured == {"x": 1}
    assert result["content"][0]["type"] == "text"
    parsed = json.loads(result["content"][0]["text"])
    assert parsed == {"echo": {"x": 1}}


def test_invoke_unknown_tool_raises_mcp_server_error() -> None:
    tools = build_default_published_tools()
    with pytest.raises(MCPServerError, match="unknown MCP tool"):
        invoke_published_tool(tools, name="nope", arguments={})


def test_invoke_handler_exception_wrapped_in_mcp_server_error() -> None:
    def raiser(_args):
        raise RuntimeError("boom")

    tool = PublishedTool(
        name="boom",
        description="t",
        input_schema={"type": "object", "additionalProperties": True},
        handler=raiser,
    )
    with pytest.raises(MCPServerError, match="failed"):
        invoke_published_tool([tool], name="boom", arguments={})


def test_invoke_returns_plain_text_for_string_result() -> None:
    tool = PublishedTool(
        name="t",
        description="t",
        input_schema={"type": "object"},
        handler=lambda _: "hello",
    )
    result = invoke_published_tool([tool], name="t", arguments={})
    assert result["content"][0]["text"] == "hello"


def test_default_tools_have_valid_json_schema_for_args() -> None:

    for tool in build_default_published_tools():
        assert tool.input_schema.get("type") == "object"
        # Tools that require an argument must declare it in `required`.
        if tool.name in (
            "openminion.plan.show",
            "openminion.search.web",
            "openminion.fetch.url",
        ):
            assert "required" in tool.input_schema
            assert len(tool.input_schema["required"]) >= 1


class _EchoArgs(BaseModel):
    x: int


def test_runtime_publish_is_opt_in() -> None:
    registry = ToolRegistry()
    runtime = SimpleNamespace(
        config=SimpleNamespace(runtime=SimpleNamespace(mcp_publish={})),
        tools=registry,
    )

    assert build_runtime_published_tools(runtime) == []


def test_runtime_publish_invokes_real_tool_registry() -> None:
    captured: dict[str, object] = {}

    def handler(args, ctx):
        captured["args"] = dict(args)
        captured["origin"] = ctx.policy.raw["context_metadata"]["origin"]
        return {
            "ok": True,
            "content": f"x={args['x']}",
            "data": {"value": args["x"]},
            "verified": True,
        }

    registry = ToolRegistry()
    registry.add(
        ToolSpec(
            name="utility.echo",
            args_model=_EchoArgs,
            min_scope="READ_ONLY",
            handler=handler,
            parameters_schema=_EchoArgs.model_json_schema(),
        )
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(
                mcp_publish={
                    "enabled": True,
                    "include_tools": ["utility.*"],
                    "exclude_tools": [],
                }
            )
        ),
        tools=registry,
        authored_tools=None,
        sandbox_runner=None,
    )

    tools = build_runtime_published_tools(runtime)
    assert [tool.name for tool in tools] == ["openminion.tool.utility.echo"]

    result = invoke_published_tool(
        tools,
        name="openminion.tool.utility.echo",
        arguments={"x": 7},
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["tool"] == "utility.echo"
    assert payload["content"] == "x=7"
    assert payload["verified"] is True
    assert payload["data"]["value"] == 7
    assert captured == {
        "args": {"x": 7},
        "origin": "mcp.server.publish",
    }


def test_runtime_publish_honors_include_exclude_scope() -> None:
    registry = ToolRegistry()
    registry.add(
        ToolSpec(
            name="file.read",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=lambda _args, _ctx: {"ok": True, "content": "read"},
        )
    )
    registry.add(
        ToolSpec(
            name="file.write",
            args_model=dict,
            min_scope="WRITE_SAFE",
            handler=lambda _args, _ctx: {"ok": True, "content": "write"},
        )
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(
                mcp_publish={
                    "enabled": True,
                    "include_tools": ["file.*"],
                    "exclude_tools": ["*.write"],
                }
            )
        ),
        tools=registry,
    )

    assert [
        tool.runtime_tool_name for tool in build_runtime_published_tools(runtime)
    ] == ["file.read"]


def test_published_mcp_jsonrpc_handler_supports_tools_list_and_call() -> None:
    tool = PublishedTool(
        name="custom",
        description="custom tool",
        input_schema={"type": "object"},
        handler=lambda args: {"echo": args},
    )

    listed = handle_published_mcp_request(
        [tool],
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert listed == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": render_tools_list_payload([tool]),
    }

    called = handle_published_mcp_request(
        [tool],
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "custom", "arguments": {"x": 1}},
        },
    )
    assert called is not None
    assert called["jsonrpc"] == "2.0"
    assert called["id"] == 2
    assert called["result"]["content"][0]["type"] == "text"
    assert json.loads(called["result"]["content"][0]["text"]) == {"echo": {"x": 1}}
