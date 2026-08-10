import json
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_TRACE_REQUESTS_ENV
from openminion.modules.llm.client_call import usage_payload_from_response_usage
from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.telemetry.constants import TRACE_HOME_ROOT_METADATA_KEY
from openminion.modules.telemetry.service import build_execution_traceparent
from openminion.modules.telemetry.trace.structured import trace_context_payload
from openminion.modules.telemetry.trace.layout import (
    build_trace_file_path,
    resolve_trace_root,
    write_protected_trace_file,
)
from openminion.modules.telemetry.trace.metadata import (
    apply_content_policy,
    merge_trace_metadata,
)
from openminion.modules.telemetry.trace.structured import write_structured_trace
from openminion.modules.llm.thinking import serialize_thinking_blocks
from openminion.modules.tool.dispatch import _get_registry_manager


def _serialize_thinking_blocks(raw_blocks: list[Any] | None) -> list[dict[str, Any]]:
    return serialize_thinking_blocks(raw_blocks)


def _public_trace_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        text_key = str(key or "")
        if text_key == TRACE_HOME_ROOT_METADATA_KEY or text_key.startswith("__trace_"):
            continue
        cleaned[text_key] = value
    return cleaned


def _trace_identity_payload(trace_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(trace_context.get("session_id", "") or ""),
        "turn_id": str(trace_context.get("turn_id", "") or ""),
        "inference_step": int(trace_context.get("inference_step", 0) or 0),
        "label": str(trace_context.get("label", "") or ""),
        "trace_id": str(trace_context.get("trace_id", "") or ""),
        "agent_id": str(trace_context.get("agent_id", "") or ""),
        "run_id": str(trace_context.get("run_id", "") or ""),
        "invocation_id": str(trace_context.get("invocation_id", "") or ""),
        "execution_id": str(trace_context.get("execution_id", "") or ""),
    }


def _trace_enabled() -> bool:
    return resolve_environment_config().get(
        OPENMINION_TRACE_REQUESTS_ENV, ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def _provider_request_tools_payload(tools) -> list[dict[str, Any]]:
    manager = _get_registry_manager()

    def _schema_for_tool_name(tool_name: str) -> dict[str, Any]:
        if manager is None or not callable(getattr(manager, "schema_for", None)):
            return {}
        try:
            schema = manager.schema_for(tool_name)
        except Exception:
            return {}
        return dict(schema) if isinstance(schema, dict) else {}

    payload: list[dict[str, Any]] = []
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "")
        payload.append(
            {
                "name": name,
                "description": str(getattr(tool, "description", "") or ""),
                "parameters": _schema_for_tool_name(name)
                or getattr(tool, "parameters", {})
                or {},
                "risk": str(getattr(tool, "risk", "") or ""),
            }
        )
    return payload


def _provider_request_payload(
    *,
    provider_request: ProviderRequest,
    label: str,
    provider_name: str,
    inbound_metadata: dict[str, Any],
    turn_id: str,
    inference_step: int,
) -> dict[str, Any]:
    return {
        "label": label,
        "provider": provider_name,
        "model": str(getattr(provider_request, "model", "") or ""),
        "system_prompt": provider_request.system_prompt,
        "user_message": provider_request.user_message,
        "history": [
            {"role": item.role, "content": item.content}
            for item in list(provider_request.history or [])
        ],
        "tools": _provider_request_tools_payload(list(provider_request.tools or [])),
        "tool_choice": getattr(provider_request, "tool_choice", "auto"),
        "tool_call_strategy": str(
            getattr(provider_request, "tool_call_strategy", "") or ""
        ),
        "metadata": _public_trace_metadata(
            dict(getattr(provider_request, "metadata", {}) or {})
        ),
        "session_id": str(inbound_metadata.get("session_id", "") or ""),
        "run_id": str(inbound_metadata.get("run_id", "") or ""),
        "invocation_id": str(inbound_metadata.get("invocation_id", "") or ""),
        "execution_id": str(inbound_metadata.get("execution_id", "") or ""),
        "turn_id": str(turn_id),
        "inference_step": inference_step,
    }


def _provider_request_raw_text(provider_request: ProviderRequest) -> str:
    raw_parts: list[str] = []
    system_prompt = str(provider_request.system_prompt or "").strip()
    if system_prompt:
        raw_parts.append(f"[system]\n{system_prompt}")
    for item in list(provider_request.history or []):
        raw_parts.append(f"[{item.role}]\n{item.content}")
    user_message = str(provider_request.user_message or "").strip()
    if user_message:
        raw_parts.append(f"[user]\n{user_message}")
    return "\n\n".join(raw_parts).strip()


def _provider_response_tool_calls(
    provider_response: ProviderResponse,
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(getattr(call, "id", "") or ""),
            "name": str(getattr(call, "name", "") or ""),
            "arguments": getattr(call, "arguments", {}) or {},
            "source": str(getattr(call, "source", "") or ""),
            "status": str(getattr(call, "status", "") or ""),
            "error": str(getattr(call, "error", "") or ""),
        }
        for call in list(getattr(provider_response, "tool_calls", []) or [])
    ]


def _provider_response_payload(
    *,
    provider_response: ProviderResponse,
    label: str,
    provider_name: str,
    inbound_metadata: dict[str, Any],
    turn_id: str,
    inference_step: int,
) -> dict[str, Any]:
    return {
        "label": label,
        "provider": provider_name,
        "model": str(getattr(provider_response, "model", "") or ""),
        "ok": bool(getattr(provider_response, "ok", True)),
        "finish_reason": str(getattr(provider_response, "finish_reason", "") or ""),
        "output_text": str(
            getattr(provider_response, "output_text", "")
            or getattr(provider_response, "text", "")
            or ""
        ),
        "thinking_blocks": _serialize_thinking_blocks(
            list(getattr(provider_response, "thinking", []) or [])
        ),
        "tool_calls": _provider_response_tool_calls(provider_response),
        "error": getattr(provider_response, "error", None),
        "session_id": str(inbound_metadata.get("session_id", "") or ""),
        "run_id": str(inbound_metadata.get("run_id", "") or ""),
        "invocation_id": str(inbound_metadata.get("invocation_id", "") or ""),
        "execution_id": str(inbound_metadata.get("execution_id", "") or ""),
        "turn_id": str(turn_id),
        "inference_step": inference_step,
    }


def trace_provider_request(
    *,
    provider_request: ProviderRequest,
    label: str,
    provider_name: str,
    home_root: Path | None,
    inbound_metadata: dict[str, Any],
    turn_id: str,
    inference_step: int,
    logger,
) -> None:
    if not _trace_enabled():
        return

    trace_root = resolve_trace_root(home_root=home_root)
    payload = _provider_request_payload(
        provider_request=provider_request,
        label=label,
        provider_name=provider_name,
        inbound_metadata=inbound_metadata,
        turn_id=turn_id,
        inference_step=inference_step,
    )
    trace_path, _ = build_trace_file_path(
        trace_root,
        session_id=payload["session_id"],
        turn_id=payload["turn_id"],
        inference_step=inference_step,
        label=label,
        suffix=".json",
    )
    raw_path, _ = build_trace_file_path(
        trace_root,
        session_id=payload["session_id"],
        turn_id=payload["turn_id"],
        inference_step=inference_step,
        label=label,
        suffix="-raw.txt",
    )
    trace_context = trace_context_payload(
        session_id=payload["session_id"],
        turn_id=payload["turn_id"],
        inference_step=inference_step,
        label=label,
        trace_id=str(inbound_metadata.get("trace_id", "") or ""),
        agent_id=str(inbound_metadata.get("agent_id", "") or ""),
        run_id=payload["run_id"],
        invocation_id=payload["invocation_id"],
        execution_id=payload["execution_id"],
        provider=provider_name,
        model=payload["model"],
        home_root=home_root,
    )
    payload["trace"] = _trace_identity_payload(trace_context)
    payload["http_trace_filename"] = trace_context["http_trace_filename"]
    payload["http_response_trace_filename"] = trace_context[
        "http_response_trace_filename"
    ]
    payload["structured_trace_filename"] = trace_context["structured_trace_filename"]
    payload = apply_content_policy(payload, allow_sensitive_content=True)
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload, indent=2, sort_keys=True),
        )
        logger.debug("trace_request: wrote %s", trace_path)
        raw_text = _provider_request_raw_text(provider_request)
        if raw_text:
            write_protected_trace_file(raw_path, raw_text + "\n")
            logger.debug("trace_request: wrote %s", raw_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace_request: failed to write trace: %s", exc)


def trace_provider_response(
    *,
    provider_response: ProviderResponse,
    label: str,
    provider_name: str,
    home_root: Path | None,
    inbound_metadata: dict[str, Any],
    turn_id: str,
    inference_step: int,
    logger,
) -> None:
    if not _trace_enabled():
        return

    trace_root = resolve_trace_root(home_root=home_root)
    payload = _provider_response_payload(
        provider_response=provider_response,
        label=label,
        provider_name=provider_name,
        inbound_metadata=inbound_metadata,
        turn_id=turn_id,
        inference_step=inference_step,
    )
    trace_path, _ = build_trace_file_path(
        trace_root,
        session_id=payload["session_id"],
        turn_id=payload["turn_id"],
        inference_step=inference_step,
        label=label,
        suffix="-response.json",
    )
    trace_context = trace_context_payload(
        session_id=payload["session_id"],
        turn_id=payload["turn_id"],
        inference_step=inference_step,
        label=label,
        trace_id=str(inbound_metadata.get("trace_id", "") or ""),
        agent_id=str(inbound_metadata.get("agent_id", "") or ""),
        run_id=payload["run_id"],
        invocation_id=payload["invocation_id"],
        execution_id=payload["execution_id"],
        provider=provider_name,
        model=payload["model"],
        home_root=home_root,
    )
    payload["trace"] = _trace_identity_payload(trace_context)
    payload["http_trace_filename"] = trace_context["http_trace_filename"]
    payload["http_response_trace_filename"] = trace_context[
        "http_response_trace_filename"
    ]
    payload["structured_trace_filename"] = trace_context["structured_trace_filename"]
    payload = apply_content_policy(payload, allow_sensitive_content=True)
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload, indent=2, sort_keys=True, default=str),
        )
        write_structured_trace(
            trace_context=trace_context,
            patch={
                "response": {
                    "ok": payload["ok"],
                    "finish_reason": payload["finish_reason"],
                    "output_text": payload["output_text"],
                    "tool_calls": payload["tool_calls"],
                    "thinking_blocks": payload.get("thinking_blocks", []),
                    "error": payload["error"],
                }
            },
        )
        logger.debug("trace_response: wrote %s", trace_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace_response: failed to write trace: %s", exc)


merge_metadata = merge_trace_metadata


def _service_port_telemetryctl(service_port: Any) -> Any | None:
    service = getattr(service_port, "_service", None)
    return getattr(service, "_telemetryctl", None)


class AgentExecutionTelemetry:
    def __init__(self, service: Any, *, inbound: Any, runtime: Any) -> None:
        self._service = service
        self._inbound = inbound
        self._runtime = runtime
        self._session_id = str(inbound.metadata.get("session_id") or "").strip()
        self._turn_id = str(
            inbound.metadata.get("turn_id")
            or inbound.metadata.get("request_id")
            or inbound.id
        )
        self._invocation_id = str(inbound.metadata.get("invocation_id") or "")
        self._execution_id = str(inbound.metadata.get("execution_id") or "")
        inbound.metadata["turn_id"] = self._turn_id
        self._started_at = time.monotonic()
        self._active = bool(
            self._session_id and getattr(service, "_telemetryctl", None) is not None
        )

    async def start(self) -> None:
        if not self._active:
            return
        traceparent = str(self._inbound.metadata.get("traceparent") or "")
        if not traceparent and self._invocation_id and self._execution_id:
            traceparent = build_execution_traceparent(
                self._invocation_id,
                self._execution_id,
            )
            self._inbound.metadata["traceparent"] = traceparent
        self._service._bind_execution_telemetry(
            session_id=self._session_id,
            turn_id=self._turn_id,
            invocation_id=self._invocation_id,
            execution_id=self._execution_id,
        )
        for event_type, payload in (
            (
                "agent.execution.started",
                {
                    "execution_id": self._execution_id,
                    "agent_name": self._service._identity_agent_id,
                    "traceparent": traceparent,
                    "tracestate": str(self._inbound.metadata.get("tracestate") or ""),
                },
            ),
            ("agent.turn.started", {"turn_operation_id": self._turn_id}),
            (
                "agent.phase.started",
                {"phase_id": f"{self._turn_id}:act", "phase": "act"},
            ),
        ):
            await self._emit(
                event_type=event_type,
                payload=payload,
                status="started",
            )
        if self._inbound.metadata.get("trace_context_status") == "invalid":
            await self._emit(
                event_type="telemetry.propagation.invalid",
                payload={"reason_code": "malformed_traceparent"},
                status="warning",
            )

    async def finish(self, response: Any) -> Any:
        if not self._active:
            return response
        duration_ms = self._duration_ms()
        for event_type, payload in (
            (
                "agent.phase.completed",
                {
                    "phase_id": f"{self._turn_id}:act",
                    "phase": "act",
                    "duration_ms": duration_ms,
                },
            ),
            (
                "agent.turn.completed",
                {"turn_operation_id": self._turn_id, "duration_ms": duration_ms},
            ),
            (
                "agent.execution.completed",
                {
                    "execution_id": self._execution_id,
                    "duration_ms": duration_ms,
                },
            ),
        ):
            await self._emit(
                event_type=event_type,
                payload=payload,
                status="completed",
            )
        self._unbind()
        return response

    async def fail(self, exc: BaseException) -> None:
        if not self._active:
            return
        duration_ms = self._duration_ms()
        error = {"type": type(exc).__name__}
        for event_type, payload in (
            (
                "agent.phase.failed",
                {
                    "phase_id": f"{self._turn_id}:act",
                    "phase": "act",
                    "duration_ms": duration_ms,
                    "error": error,
                },
            ),
            (
                "agent.turn.failed",
                {
                    "turn_operation_id": self._turn_id,
                    "duration_ms": duration_ms,
                    "error": error,
                },
            ),
            (
                "agent.execution.failed",
                {
                    "execution_id": self._execution_id,
                    "duration_ms": duration_ms,
                    "error": error,
                },
            ),
        ):
            await self._emit(
                event_type=event_type,
                payload=payload,
                status="failed",
            )
        self._unbind()

    async def _emit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        status: str,
    ) -> None:
        await self._service._emit_agent_event(
            session_id=self._session_id,
            turn_id=self._turn_id,
            event_type=event_type,
            payload=payload,
            status=status,
        )

    def _duration_ms(self) -> float:
        return (time.monotonic() - self._started_at) * 1000

    def _unbind(self) -> None:
        self._service._unbind_execution_telemetry(
            session_id=self._session_id,
            turn_id=self._turn_id,
        )


async def generate_with_provider_call_telemetry(
    *,
    service_port: Any,
    request: ProviderRequest,
    session_id: str,
    turn_id: str,
    provider_name: str,
    generate: Callable[[], Awaitable[ProviderResponse]],
) -> ProviderResponse:
    telemetryctl = _service_port_telemetryctl(service_port)
    if telemetryctl is None or not session_id:
        return await generate()
    llm_call_id = str(uuid4())
    started_at = time.monotonic()
    await telemetryctl.emit_canonical_event(
        session_id,
        turn_id,
        "llm.call.started",
        {
            "llm_call_id": llm_call_id,
            "model": str(getattr(request, "model", "") or ""),
            "provider_name": provider_name,
            "purpose": str(request.metadata.get("purpose") or "act"),
        },
        status="started",
    )
    try:
        response = await generate()
    finally:
        if exc := sys.exception():
            await telemetryctl.emit_canonical_event(
                session_id,
                turn_id,
                "llm.call.failed",
                {
                    "llm_call_id": llm_call_id,
                    "provider_round_trip_ms": (time.monotonic() - started_at) * 1000,
                    "error": {"type": type(exc).__name__},
                },
                status="failed",
            )
    await telemetryctl.emit_canonical_event(
        session_id,
        turn_id,
        "llm.call.completed",
        {
            "llm_call_id": llm_call_id,
            "response_model": str(getattr(response, "model", "") or ""),
            "provider_round_trip_ms": (time.monotonic() - started_at) * 1000,
            "usage": usage_payload_from_response_usage(
                getattr(response, "usage", None)
            ),
            "finish_reason": str(getattr(response, "finish_reason", "") or ""),
        },
        status="completed",
    )
    return response
