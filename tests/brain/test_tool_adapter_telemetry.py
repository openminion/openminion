from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.tool.registry import ToolRegistry, ToolSpec


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["success", "failure"]


class _Telemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit_canonical_event(
        self,
        _session_id: str,
        _turn_id: str,
        event_type: str,
        payload: dict,
        **_kwargs: object,
    ) -> None:
        self.events.append((event_type, payload))


def test_tool_adapter_emits_one_generic_lifecycle_per_handler_call(
    tmp_path,
) -> None:
    def handler(args: dict, _context) -> dict:
        if args["outcome"] == "success":
            return {"ok": True, "state": "succeeded"}
        return {
            "ok": False,
            "state": "failed",
            "error": {"code": "EXPECTED_FAILURE", "message": "expected"},
        }

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="blockchain.debug",
            args_model=_Args,
            min_scope="READ_ONLY",
            handler=handler,
        )
    )
    telemetry = _Telemetry()
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        telemetryctl=telemetry,
    )

    for call_id, outcome in (("call-1", "success"), ("call-2", "failure")):
        adapter.execute(
            command={
                "command_id": call_id,
                "tool_name": "blockchain.debug",
                "args": {"outcome": outcome},
            },
            session_id="session",
            trace_id="turn",
        )

    assert telemetry.events == [
        (
            "tool.execution.started",
            {"tool_call_id": "call-1", "tool_name": "blockchain.debug"},
        ),
        (
            "tool.execution.completed",
            {"tool_call_id": "call-1", "tool_name": "blockchain.debug"},
        ),
        (
            "tool.execution.started",
            {"tool_call_id": "call-2", "tool_name": "blockchain.debug"},
        ),
        (
            "tool.execution.failed",
            {"tool_call_id": "call-2", "tool_name": "blockchain.debug"},
        ),
    ]


def test_tool_adapter_closes_raised_handler_lifecycle_without_exposing_text(
    tmp_path,
) -> None:
    def handler(_args: dict, _context) -> dict:
        raise RuntimeError("private provider text")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="blockchain.debug",
            args_model=_Args,
            min_scope="READ_ONLY",
            handler=handler,
        )
    )
    telemetry = _Telemetry()
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        telemetryctl=telemetry,
    )

    result = adapter.execute(
        command={
            "command_id": "call-raised",
            "tool_name": "blockchain.debug",
            "args": {"outcome": "success"},
        },
        session_id="session",
        trace_id="turn",
    )

    assert result["error"] == {
        "code": "EXEC_ERROR",
        "message": "Tool execution failed",
    }
    assert "private provider text" not in str(result)
    assert telemetry.events == [
        (
            "tool.execution.started",
            {"tool_call_id": "call-raised", "tool_name": "blockchain.debug"},
        ),
        (
            "tool.execution.failed",
            {"tool_call_id": "call-raised", "tool_name": "blockchain.debug"},
        ),
    ]
