from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool import (
    build_runtime_tool_routing_metadata,
    resolve_runtime_tool_config,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.task.routine.dispatcher import PreTurnContext


class ToolRegistryPreTurnContext(PreTurnContext):
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        routine_id: str = "",
        session_id: str = "",
        agent_id: str = "",
        allowed_tools: tuple[str, ...] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._registry = registry
        self._routine_id = str(routine_id or "").strip()
        self._session_id = str(session_id or "").strip()
        self._agent_id = str(agent_id or "").strip()
        self._allowed_tools = (
            frozenset(allowed_tools) if allowed_tools is not None else None
        )
        self._metadata = dict(metadata or {})

    def exact_provider_enabled(self, *, family: str, provider_id: str) -> bool:
        context = ToolExecutionContext(
            channel="cron",
            target=self._routine_id or "routine",
            metadata=self._metadata,
        )
        config = getattr(resolve_runtime_tool_config(context), family, None)
        if config is None or config.allow_fallback is not False:
            return False
        normalized = provider_id.strip().lower()
        enabled = {item.strip().lower() for item in config.enabled_providers}
        return (
            normalized in enabled
            and config.default_provider.strip().lower() == normalized
        )

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._allowed_tools is not None and name not in self._allowed_tools:
            return {
                "ok": False,
                "error": {
                    "code": "POLICY_DENIED",
                    "message": f"Tool {name!r} is not allowed for this routine.",
                    "details": {"reason_code": "routine_tool_not_allowed"},
                },
            }
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

        metadata: dict[str, Any] = {
            **self._metadata,
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
            result_data = dict(result.data) if isinstance(result.data, Mapping) else {}
            nested_details = result_data.get("details")
            details = (
                dict(nested_details)
                if isinstance(nested_details, Mapping)
                else {
                    key: value
                    for key, value in result_data.items()
                    if key != "error_code"
                }
            )
            normalized["error"] = {
                "message": result.error,
                "code": str(result_data.get("error_code", "TOOL_EXEC_FAILED")),
                "details": details,
            }
        return normalized


def build_routine_pre_turn_context(
    *,
    runtime: Any,
    routine_id: str,
    session_id: str,
    agent_id: str,
    allowed_tools: tuple[str, ...],
) -> ToolRegistryPreTurnContext | None:
    registry = getattr(runtime, "tools", None)
    if registry is None:
        return None
    runtime_config = getattr(getattr(runtime, "config", None), "runtime", None)
    return ToolRegistryPreTurnContext(
        registry=registry,
        routine_id=routine_id,
        session_id=session_id,
        agent_id=agent_id,
        allowed_tools=allowed_tools,
        metadata=build_runtime_tool_routing_metadata(
            getattr(runtime_config, "tools", None)
        ),
    )


def write_routine_artifact(
    *,
    artifactctl_factory: Any,
    routine_id: str,
    body: str,
    mime: str,
    session_id: str,
    agent_id: str,
) -> str:
    artifactctl = artifactctl_factory()
    try:
        suffix = ".json" if mime == "application/json" else ".md"
        artifact = artifactctl.ingest_bytes(
            body.encode("utf-8"),
            mime=mime,
            original_name=f"routine-{routine_id}{suffix}",
            label=f"routine:{routine_id}",
            meta={"routine_id": routine_id},
            session_id=session_id or None,
            agent_id=agent_id or None,
        )
        artifact_id = str(artifact.ref)
        artifactctl.ref_add(
            "session", session_id or f"routine:{routine_id}", artifact_id
        )
        return artifact_id
    finally:
        close = getattr(artifactctl, "close", None)
        if callable(close):
            close()


__all__ = [
    "ToolRegistryPreTurnContext",
    "build_routine_pre_turn_context",
    "write_routine_artifact",
]
