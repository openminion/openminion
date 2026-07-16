"""Tool-run helpers for the developer API."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.refs import (
    tool_result_artifact_refs as _tool_result_artifact_refs,
)
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from openminion.services.security.blast_radius.wiring import (
    SEAM_API_TOOLS,
    build_default_composition_boundary_adapter,
)
from openminion.services.tool.selection import ToolSelectionService

_API_TOOLS_DEFAULT_CHANNEL = "console"
_API_TOOLS_DEFAULT_TARGET = "api-user"
_API_TOOLS_DEFAULT_SESSION_ID = "tools"


def normalize_tool_run_request(body: dict[str, Any]) -> dict[str, Any]:
    channel = (
        str(body.get("channel", _API_TOOLS_DEFAULT_CHANNEL)).strip()
        or _API_TOOLS_DEFAULT_CHANNEL
    )
    target = (
        str(body.get("target", _API_TOOLS_DEFAULT_TARGET)).strip()
        or _API_TOOLS_DEFAULT_TARGET
    )
    requested_session_id = (
        str(body.get("session_id", _API_TOOLS_DEFAULT_SESSION_ID)).strip()
        or _API_TOOLS_DEFAULT_SESSION_ID
    )
    return {
        "channel": channel,
        "target": target,
        "requested_session_id": requested_session_id,
    }


def execute_tool_run(
    *,
    runtime,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: str,
    channel: str,
    target: str,
    requested_session_id: str,
) -> tuple[HTTPStatus, dict[str, Any], str]:
    session = runtime.sessions.resolve_session(
        agent_id=resolve_default_agent_id(runtime.config),
        channel=channel,
        target=target,
        session_id=requested_session_id,
    )
    context = ToolExecutionContext(
        channel=channel,
        target=target,
        session_id=session.id,
        authored_tools_api=getattr(runtime, "authored_tools", None),
        metadata={
            "trace_id": request_id,
            "session_id": session.id,
            "origin": "api.v1.tools.run",
            "runtime_env": dict(
                getattr(
                    getattr(runtime.config, "runtime", None),
                    "env",
                    {},
                )
                or {}
            ),
            **build_runtime_tool_routing_metadata(runtime.config.runtime.tools),
            **ToolSelectionService(
                runtime.config.runtime.tool_selection,
                runtime.tools,
            ).runtime_binding_policy_metadata(),
        },
        blast_radius_adapter=build_default_composition_boundary_adapter(
            seam_id=SEAM_API_TOOLS,
        ),
    )
    batch = runtime.tools.execute_calls(
        [
            ProviderToolCall(
                name=tool_name,
                arguments=arguments,
                id=request_id,
                source="daemon_api",
            )
        ],
        context=context,
    )
    result = batch.results[0]
    artifact_refs = _tool_result_artifact_refs(
        trace_id=request_id,
        session_id=session.id,
        result=result,
    )
    runtime.sessions.append_event(
        session_id=session.id,
        event_type="tool.run",
        payload={
            "trace_id": request_id,
            "tool": result.tool_name,
            "ok": bool(result.ok),
            "verified": bool(result.verified),
            "artifact_refs": artifact_refs,
        },
    )
    status = HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST
    payload = {
        "ok": bool(result.ok),
        "trace_id": request_id,
        "artifact_refs": artifact_refs,
        "tool": {
            "name": result.tool_name,
            "ok": bool(result.ok),
            "verified": bool(result.verified),
            "content": result.content,
            "error": result.error,
            "data": dict(result.data or {}),
            "call_id": result.call_id,
            "source": result.source,
        },
    }
    return status, payload, session.id
