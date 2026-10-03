"""Tool-run helpers for the developer API."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Any, cast

from openminion.base.config.core import resolve_default_agent_id
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.exposure.service import (
    SECURITY_LAB_ALLOWED_TOOL_IDS,
    project_security_lab_metadata,
    resolve_security_lab_metadata,
)
from openminion.modules.tool.refs import tool_result_artifact_refs
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from openminion.modules.policy.adapters.composition import (
    SEAM_API_TOOLS,
    build_default_composition_boundary_adapter,
)
from openminion.modules.tool.selection import ToolSelectionService

_API_TOOLS_DEFAULT_CHANNEL = "console"
_API_TOOLS_DEFAULT_TARGET = "api-user"
_API_TOOLS_DEFAULT_SESSION_ID = "tools"


def _security_lab_tool_context(
    runtime: Any,
    session: Any,
) -> tuple[Any, Callable[[], dict[str, Any]], dict[str, Any]]:
    lab_config = runtime.config.runtime.security_lab
    if lab_config is None:
        return None, lambda: {}, {}
    agent_id = str(session.owner_agent_id or resolve_default_agent_id(runtime.config))
    agent_service = runtime.resolve_agent_service(agent_id)
    identity = cast(
        dict[str, Any],
        agent_service._identity_security_lab_facts(),  # noqa: SLF001
    )
    lab_runner = getattr(runtime, "security_lab_runner", None)

    def metadata() -> dict[str, Any]:
        return cast(
            dict[str, Any],
            resolve_security_lab_metadata(
                runtime.tools.exposure_service,
                config=lab_config,
                runner=lab_runner,
                identity=agent_service._identity_security_lab_facts(),  # noqa: SLF001
                session_id=session.id,
            ),
        )

    return lab_runner, metadata, identity


def _security_lab_tool_denial(
    identity: dict[str, Any], tool_name: str
) -> ToolExecutionResult | None:
    if not identity.get("lab_required"):
        return None
    exact_posture = (
        identity.get("tool_use") == "restricted"
        and frozenset(identity.get("allowed_tools", ()))
        == SECURITY_LAB_ALLOWED_TOOL_IDS
    )
    if exact_posture and tool_name in SECURITY_LAB_ALLOWED_TOOL_IDS:
        return None
    return ToolExecutionResult(
        tool_name=tool_name,
        ok=False,
        content="",
        error="Tool is not allowed for the security lab identity",
        data={"reason_code": "security_lab_tool_not_allowed"},
    )


def _resolve_tool_run_session(
    runtime: Any,
    *,
    channel: str,
    target: str,
    requested_session_id: str,
) -> Any:
    agent_id = resolve_default_agent_id(runtime.config)
    lab_config = runtime.config.runtime.security_lab
    if lab_config is not None:
        existing = runtime.sessions.get_session(requested_session_id)
        if (
            existing is not None
            and existing.owner_agent_id == lab_config.agent_identity_id
        ):
            agent_id = existing.owner_agent_id
    return runtime.sessions.resolve_session(
        agent_id=agent_id,
        channel=channel,
        target=target,
        session_id=requested_session_id,
    )


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
        "confirm": bool(body.get("confirm", False)),
    }


def _tool_run_response(
    runtime: Any,
    *,
    request_id: str,
    session_id: str,
    result: ToolExecutionResult,
) -> tuple[HTTPStatus, dict[str, Any], str]:
    artifact_refs = tool_result_artifact_refs(
        trace_id=request_id,
        session_id=session_id,
        result=result,
    )
    runtime.sessions.append_event(
        session_id=session_id,
        event_type="tool.run",
        payload={
            "trace_id": request_id,
            "tool": result.tool_name,
            "ok": result.ok,
            "verified": result.verified,
            "artifact_refs": artifact_refs,
        },
    )
    status = HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST
    payload = {
        "ok": result.ok,
        "trace_id": request_id,
        "artifact_refs": artifact_refs,
        "tool": {
            "name": result.tool_name,
            "ok": result.ok,
            "verified": result.verified,
            "content": result.content,
            "error": result.error,
            "data": dict(result.data or {}),
            "call_id": result.call_id,
            "source": result.source,
        },
    }
    return status, payload, session_id


def execute_tool_run(
    *,
    runtime,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: str,
    channel: str,
    target: str,
    requested_session_id: str,
    confirm: bool = False,
) -> tuple[HTTPStatus, dict[str, Any], str]:
    session = _resolve_tool_run_session(
        runtime,
        channel=channel,
        target=target,
        requested_session_id=requested_session_id,
    )
    workspace_root = getattr(runtime, "tool_workspace_root", None)
    workspace_root = workspace_root or runtime.config.runtime.tool_workspace_root
    runtime_env: dict[str, Any] = dict(runtime.config.runtime.env)
    lab_runner, security_lab_metadata, identity = _security_lab_tool_context(
        runtime, session
    )
    if identity.get("lab_required"):
        workspace_root = "/workspace"
        runtime_env = {}

    metadata: dict[str, Any] = {
        "trace_id": request_id,
        "session_id": session.id,
        "origin": "api.v1.tools.run",
        "workspace_root": str(workspace_root or ""),
        "runtime_env": runtime_env,
        **build_runtime_tool_routing_metadata(runtime.config.runtime.tools),
        **ToolSelectionService(
            runtime.config.runtime.tool_selection,
            runtime.tools,
        ).runtime_binding_policy_metadata(),
    }
    project_security_lab_metadata(metadata, security_lab_metadata())
    context = ToolExecutionContext(
        channel=channel,
        target=target,
        session_id=session.id,
        authored_tools_api=getattr(runtime, "authored_tools", None),
        security_lab_runner=lab_runner,
        security_lab_metadata=security_lab_metadata,
        metadata=metadata,
        blast_radius_adapter=build_default_composition_boundary_adapter(
            seam_id=SEAM_API_TOOLS,
        ),
        confirm=confirm,
    )
    result = _security_lab_tool_denial(identity, tool_name)
    if result is None:
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
    return _tool_run_response(
        runtime,
        request_id=request_id,
        session_id=session.id,
        result=result,
    )
