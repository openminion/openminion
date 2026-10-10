from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from openminion.api.operations.tools import _tool_run_response
from openminion.cli.interactive.runtime.messages import RuntimeMessageMixin
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.mcp.server import (
    build_runtime_published_tools,
    invoke_published_tool,
)


class _StatusArgs(BaseModel):
    state: str


_STATES = (
    "pending",
    "succeeded",
    "reverted",
    "reorged",
    "postcondition_failed",
    "broadcast_unknown",
)


def _facts(state: str) -> dict[str, object]:
    return {
        "preparation_digest": "sha256:" + "1" * 64,
        "transaction_hash": "0x" + "2" * 64,
        "state": state,
        "broadcast_attempts": 1,
        "confirmation_depth": 2 if state == "succeeded" else 0,
        "finality_reached": state == "succeeded",
        "reorg_detected": state == "reorged",
        "postcondition_results": (
            [{"matched": False}] if state == "postcondition_failed" else []
        ),
        "error": (
            {"code": "TRANSACTION_REVERTED"}
            if state == "reverted"
            else {"code": "BROADCAST_OUTCOME_UNKNOWN"}
            if state == "broadcast_unknown"
            else None
        ),
    }


def _tool() -> ToolSpec:
    def handler(args, _context):
        facts = _facts(args["state"])
        return {
            "ok": True,
            "content": json.dumps(facts, sort_keys=True),
            "data": facts,
            "verified": True,
        }

    return ToolSpec(
        name="blockchain.inspect",
        args_model=_StatusArgs,
        min_scope="READ_ONLY",
        handler=handler,
        parameters_schema=_StatusArgs.model_json_schema(),
    )


def _runtime(registry: ToolRegistry) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(
                mcp_publish={
                    "enabled": True,
                    "include_tools": ["blockchain.inspect"],
                }
            )
        ),
        tools=registry,
        authored_tools=None,
        sandbox_runner=None,
    )


@pytest.mark.parametrize("state", _STATES)
def test_operation_status_facts_survive_supported_surfaces(state: str) -> None:
    expected = _facts(state)
    registry = ToolRegistry()
    tool = _tool()
    registry.add(tool)
    result = execute_tool_spec_call(
        tool=tool,
        arguments={"state": state},
        context=ToolExecutionContext(channel="python", target="test"),
    )
    assert result.ok is True
    assert result.data == expected

    events: list[dict] = []
    runtime = SimpleNamespace(
        sessions=SimpleNamespace(
            append_event=lambda **event: events.append(event),
        )
    )
    status, payload, session_id = _tool_run_response(
        runtime,
        request_id="request-1",
        session_id="session-1",
        result=result,
    )
    assert status == 200
    assert session_id == "session-1"
    assert payload["tool"]["data"] == expected
    assert events[0]["payload"]["tool"] == "blockchain.inspect"

    published = build_runtime_published_tools(_runtime(registry))
    assert [item.runtime_tool_name for item in published] == ["blockchain.inspect"]
    mcp_result = invoke_published_tool(
        published,
        name=published[0].name,
        arguments={"state": state},
    )
    assert "structuredContent" not in mcp_result
    mcp_payload = json.loads(mcp_result["content"][0]["text"])
    assert {key: mcp_payload["data"][key] for key in expected} == expected

    adapter = object.__new__(RuntimeMessageMixin)
    event = adapter._tool_event_from_payload(
        {
            "tool_name": "blockchain.inspect",
            "content": result.content,
        }
    )
    assert event is not None
    assert json.loads(event.content) == expected
