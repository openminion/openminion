from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openminion.api.operations.tools import _tool_run_response
from openminion.base.config.env import EnvironmentConfig
from openminion.base.config.runtime.tools import BlockchainToolRuntimeConfig
from openminion.cli.interactive.runtime.messages import RuntimeMessageMixin
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.blockchain import plugin as blockchain_plugin
from openminion.tools.blockchain.preparations import claim_operation_record
from openminion.tools.blockchain.resolved_schemas import OperationStatusData
from openminion.tools.blockchain.transaction_schemas import (
    operation_digest,
    validate_operation_record,
)
from openminion.tools.mcp.server import (
    build_runtime_published_tools,
    invoke_published_tool,
)


_STATES = (
    "pending",
    "succeeded",
    "reverted",
    "reorged",
    "postcondition_failed",
    "broadcast_unknown",
)


def _operation(state: str) -> dict[str, object]:
    has_receipt = state in {
        "succeeded",
        "reverted",
        "reorged",
        "postcondition_failed",
    }
    postcondition_results = (
        [
            {
                "function_signature": "balanceOf(address)",
                "arguments": ["0x" + "4" * 40],
                "expected_result": ["10"],
                "actual_result": ["9"],
                "matched": False,
                "error_code": None,
            }
        ]
        if state == "postcondition_failed"
        else []
    )
    operation: dict[str, object] = {
        "schema_version": 1,
        "preparation_digest": "sha256:" + "1" * 64,
        "resolution_digest": "sha256:" + "2" * 64,
        "operation_digest": "sha256:" + "0" * 64,
        "transaction_hash": "0x" + "3" * 64,
        "submission_started": True,
        "broadcast_attempts": 1,
        "state": state,
        "receipt_block_number": "10" if has_receipt else None,
        "receipt_block_hash": "0x" + "5" * 64 if has_receipt else None,
        "receipt_status": 0 if state == "reverted" else 1 if has_receipt else None,
        "confirmations": 2 if has_receipt else 0,
        "confirmation_depth": 2,
        "gas_used": "21000" if has_receipt else None,
        "effective_gas_price_wei": "1" if has_receipt else None,
        "postconditions": [],
        "postcondition_results": postcondition_results,
        "last_error_code": (
            "POSTCONDITION_FAILED" if state == "postcondition_failed" else None
        ),
    }
    operation["operation_digest"] = operation_digest(operation)
    return validate_operation_record(operation)


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
def test_operation_status_facts_survive_supported_surfaces(
    state: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.tools.blockchain import resolved_operations, runtime

    session_id = f"surface-{state}"
    runtime_env = {
        "OPENMINION_HOME": str(tmp_path),
        "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
    }
    monkeypatch.setenv("OPENMINION_HOME", runtime_env["OPENMINION_HOME"])
    monkeypatch.setenv("OPENMINION_DATA_ROOT", runtime_env["OPENMINION_DATA_ROOT"])
    monkeypatch.setattr(
        runtime,
        "resolve_blockchain_config",
        lambda _context: BlockchainToolRuntimeConfig(enabled=True),
    )
    monkeypatch.setattr(
        resolved_operations,
        "_load_operation_status_facts",
        lambda digest, context: (
            resolved_operations._load_operation(digest, context),
            {},
            {"block_number": "10", "block_hash": "0x" + "6" * 64},
            None,
        ),
    )
    monkeypatch.setattr(
        resolved_operations,
        "_observe_operation_receipt",
        lambda _operation, _record: (None, None),
    )

    operation = _operation(state)
    claim_operation_record(
        operation,
        SimpleNamespace(
            session_id=session_id,
            env=EnvironmentConfig(values=runtime_env),
        ),
        validator=validate_operation_record,
        digester=operation_digest,
    )
    expected = OperationStatusData.model_validate(
        {key: operation[key] for key in OperationStatusData.model_fields}
    ).model_dump(mode="json")

    registry = ToolRegistry()
    blockchain_plugin.register(registry)
    tool = registry.get("blockchain.inspect")
    arguments = {
        "action": "operation_status",
        "preparation_digest": operation["preparation_digest"],
    }
    result = execute_tool_spec_call(
        tool=tool,
        arguments=arguments,
        context=ToolExecutionContext(
            channel="python",
            target="test",
            session_id=session_id,
            metadata={"runtime_env": runtime_env},
        ),
    )
    assert result.ok is True
    assert result.data == expected

    events: list[dict] = []
    api_runtime = SimpleNamespace(
        sessions=SimpleNamespace(
            append_event=lambda **event: events.append(event),
        )
    )
    status, payload, returned_session_id = _tool_run_response(
        api_runtime,
        request_id="request-1",
        session_id=session_id,
        result=result,
    )
    assert status == 200
    assert returned_session_id == session_id
    assert payload["tool"]["data"] == expected
    assert events[0]["payload"]["tool"] == "blockchain.inspect"

    published = build_runtime_published_tools(_runtime(registry))
    assert [item.runtime_tool_name for item in published] == ["blockchain.inspect"]
    mcp_result = invoke_published_tool(
        published,
        name=published[0].name,
        arguments={**arguments, "_session_id": session_id},
    )
    assert "structuredContent" not in mcp_result
    mcp_payload = json.loads(mcp_result["content"][0]["text"])
    assert {
        key: mcp_payload["data"][key] for key in OperationStatusData.model_fields
    } == expected

    adapter = object.__new__(RuntimeMessageMixin)
    event = adapter._tool_event_from_payload(
        {
            "tool_name": "blockchain.inspect",
            "content": result.content,
        }
    )
    assert event is not None
    assert json.loads(event.content) == expected
