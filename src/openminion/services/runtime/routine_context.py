from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.task.routine.dispatcher import PostTurnSink, PreTurnContext


class ToolRegistryPreTurnContext(PreTurnContext):
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        routine_id: str = "",
        session_id: str = "",
        agent_id: str = "",
    ) -> None:
        self._registry = registry
        self._routine_id = str(routine_id or "").strip()
        self._session_id = str(session_id or "").strip()
        self._agent_id = str(agent_id or "").strip()

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = self._registry.list().get(name)
        if spec is None:
            return {
                "ok": False,
                "error": {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": f"Tool {name!r} is not registered.",
                    "details": {"reason_code": "tool_not_registered"},
                },
            }

        metadata: dict[str, str] = {
            "invocation_source": "routine_pre_turn",
            "routine_id": self._routine_id,
        }
        if self._agent_id:
            metadata["agent_id"] = self._agent_id
        tool_ctx = ToolExecutionContext(
            channel="cron",
            target=self._routine_id or "routine",
            session_id=self._session_id,
            metadata=metadata,
        )

        try:
            result = execute_tool_spec_call(
                tool=spec, arguments=dict(args), context=tool_ctx
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_EXEC_FAILED",
                    "message": str(exc),
                    "details": {
                        "reason_code": "tool_exec_failed",
                        "tool_name": name,
                    },
                },
            }

        normalized: dict[str, Any] = {
            "ok": bool(result.ok),
            "data": dict(result.data) if isinstance(result.data, Mapping) else {},
        }
        if result.error:
            normalized["error"] = {
                "message": result.error,
                "code": str((result.data or {}).get("error_code", "TOOL_EXEC_FAILED")),
            }
        return normalized


class CronRunRoutineSink(PostTurnSink):
    def __init__(self) -> None:
        self.artifact_id: str | None = None
        self.artifact_body: str | None = None
        self.announce_summary: str | None = None
        self.write_count = 0
        self.announce_count = 0

    def write_artifact(self, *, routine_id: str, body: str) -> str:
        self.write_count += 1
        self.artifact_id = f"artifact://routine/{routine_id}/run-{self.write_count}"
        self.artifact_body = body
        return self.artifact_id

    def announce(self, *, routine_id: str, summary: str) -> None:
        self.announce_count += 1
        self.announce_summary = summary


__all__ = ["ToolRegistryPreTurnContext", "CronRunRoutineSink"]
