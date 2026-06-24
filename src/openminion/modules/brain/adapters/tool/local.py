from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_PENDING,
)
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas import new_uuid


class LocalToolAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "echo"},
            {"name": "fail"},
            {"name": "sleep_async"},
            {"name": "create_artifact"},
        ]

    def execute(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]:
        tool_name = str(command.get("tool_name", "unknown"))
        args = command.get("args", {}) if isinstance(command.get("args"), dict) else {}

        if tool_name == "fail":
            return {
                "status": BRAIN_ACTION_STATUS_FAILED,
                "summary": "Tool failed by request.",
                "outputs": {},
                "error": {"code": "TOOL_FAILURE", "message": "Simulated failure"},
            }
        if tool_name == "sleep_async":
            return {
                "status": BRAIN_JOB_STATUS_PENDING,
                "task_id": new_uuid(),
                "poll_after_ms": 1000,
                "summary": "Async job created",
            }
        if tool_name == "echo":
            return {
                "status": BRAIN_ACTION_STATUS_SUCCESS,
                "summary": "Echo tool executed.",
                "outputs": {"echo": args},
                "artifact_refs": [],
                "memory_refs": [],
                "metrics": {"latency_ms": 1, "tokens_used": 0, "cost_estimate": 0.0},
            }
        if tool_name == "create_artifact":
            return {
                "status": BRAIN_ACTION_STATUS_SUCCESS,
                "summary": "Artifact created",
                "outputs": {"id": "art_123"},
                "artifact_refs": [{"ref": "art_123", "role": "output"}],
                "memory_refs": [],
                "metrics": {"latency_ms": 5, "tokens_used": 0, "cost_estimate": 0.0},
            }
        return {
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": f"Executed tool '{tool_name}'.",
            "outputs": {
                "tool_name": tool_name,
                "args": args,
                "session_id": session_id,
                "trace_id": trace_id,
            },
            "artifact_refs": [],
            "memory_refs": [],
            "metrics": {"latency_ms": 1, "tokens_used": 0, "cost_estimate": 0.0},
        }
