import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_TRACE_REQUESTS_ENV
from openminion.modules.llm.client_call import usage_payload_from_response_usage
from openminion.modules.llm.providers.base import ProviderError
from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.telemetry.constants import TRACE_HOME_ROOT_METADATA_KEY
from openminion.modules.telemetry.execution_lifecycle import (
    InvocationLifecycleFact as InvocationLifecycleFact,
)
from openminion.modules.telemetry.trace.structured import (
    TraceArtifactPublication,
    trace_context_payload,
)
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
) -> TraceArtifactPublication:
    if not _trace_enabled():
        return TraceArtifactPublication()

    trace_root = resolve_trace_root(home_root=home_root)
    payload = _provider_request_payload(
        provider_request=provider_request,
        label=label,
        provider_name=provider_name,
        inbound_metadata=inbound_metadata,
        turn_id=turn_id,
        inference_step=inference_step,
    )
    trace_path, trace_relative = build_trace_file_path(
        trace_root,
        session_id=payload["session_id"],
        turn_id=payload["turn_id"],
        inference_step=inference_step,
        label=label,
        suffix=".json",
    )
    raw_path, raw_relative = build_trace_file_path(
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
    published: list[str] = []
    complete = True
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload, indent=2, sort_keys=True),
        )
        published.append(trace_relative)
        logger.debug("trace_request: wrote %s", trace_path)
    except (OSError, TypeError, ValueError) as exc:
        complete = False
        logger.warning("trace_request: failed to write trace: %s", exc)
    raw_text = _provider_request_raw_text(provider_request)
    if raw_text:
        try:
            write_protected_trace_file(raw_path, raw_text + "\n")
            published.append(raw_relative)
            logger.debug("trace_request: wrote %s", raw_path)
        except (OSError, TypeError, ValueError) as exc:
            complete = False
            logger.warning("trace_request: failed to write raw trace: %s", exc)
    return TraceArtifactPublication(tuple(sorted(published)), complete)


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
) -> TraceArtifactPublication:
    if not _trace_enabled():
        return TraceArtifactPublication()

    trace_root = resolve_trace_root(home_root=home_root)
    payload = _provider_response_payload(
        provider_response=provider_response,
        label=label,
        provider_name=provider_name,
        inbound_metadata=inbound_metadata,
        turn_id=turn_id,
        inference_step=inference_step,
    )
    trace_path, trace_relative = build_trace_file_path(
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
    published: list[str] = []
    complete = True
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload, indent=2, sort_keys=True, default=str),
        )
        published.append(trace_relative)
        logger.debug("trace_response: wrote %s", trace_path)
    except (OSError, TypeError, ValueError) as exc:
        complete = False
        logger.warning("trace_response: failed to write trace: %s", exc)
    try:
        structured_relative = write_structured_trace(
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
        if structured_relative:
            published.append(structured_relative)
    except (OSError, TypeError, ValueError) as exc:
        complete = False
        logger.warning("trace_response: failed to write structured trace: %s", exc)
    return TraceArtifactPublication(tuple(sorted(published)), complete)


merge_metadata = merge_trace_metadata


def _service_port_telemetryctl(service_port: Any) -> Any | None:
    service = getattr(service_port, "_service", None)
    return getattr(service, "_telemetryctl", None)


def _llm_correlation_fields(request: ProviderRequest) -> dict[str, str]:
    metadata = dict(request.metadata or {})
    return {
        key: str(metadata.get(key) or "")
        for key in ("request_id", "trace_id", "run_id", "invocation_id")
        if metadata.get(key)
    }


def _llm_error_payload(error: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": type(error).__name__}
    if isinstance(error, ProviderError):
        payload.update(
            code=error.code,
            message=error.message,
            details=dict(error.details),
        )
    return payload


async def _emit_llm_call_event(
    telemetryctl: Any,
    *,
    session_id: str,
    turn_id: str,
    event_type: str,
    payload: dict[str, Any],
    status: str,
    service_port: Any,
) -> None:
    try:
        await telemetryctl.emit_canonical_event(
            session_id,
            turn_id,
            event_type,
            payload,
            status=status,
        )
    except Exception:
        service = getattr(service_port, "_service", None)
        logger = getattr(service, "_logger", logging.getLogger(__name__))
        logger.warning("llm call telemetry emit failed event_type=%s", event_type)


async def generate_with_provider_call_telemetry(
    *,
    service_port: Any,
    request: ProviderRequest,
    session_id: str,
    turn_id: str,
    provider_name: str,
    service_vendor: str = "",
    generate: Callable[[], Awaitable[ProviderResponse]],
    trace_publication: Callable[[], TraceArtifactPublication] | None = None,
) -> ProviderResponse:
    telemetryctl = _service_port_telemetryctl(service_port)
    if telemetryctl is None or not session_id:
        return await generate()
    llm_call_id = str(uuid4())
    correlation = _llm_correlation_fields(request)
    started_at = time.monotonic()
    publication = (
        trace_publication()
        if trace_publication
        else TraceArtifactPublication(complete=False)
    )
    await _emit_llm_call_event(
        telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        event_type="llm.call.started",
        payload={
            "llm_call_id": llm_call_id,
            "model": str(getattr(request, "model", "") or ""),
            "provider_name": provider_name,
            "service_vendor": service_vendor or provider_name,
            "purpose": str(request.metadata.get("purpose") or "act"),
            **correlation,
            **publication.event_fields(final=False),
        },
        status="started",
        service_port=service_port,
    )
    try:
        response = await generate()
    finally:
        if exc := sys.exception():
            publication = (
                trace_publication()
                if trace_publication
                else TraceArtifactPublication(complete=False)
            )
            await _emit_llm_call_event(
                telemetryctl,
                session_id=session_id,
                turn_id=turn_id,
                event_type="llm.call.failed",
                payload={
                    "llm_call_id": llm_call_id,
                    "provider_name": provider_name,
                    "service_vendor": service_vendor or provider_name,
                    "provider_round_trip_ms": (time.monotonic() - started_at) * 1000,
                    "error": _llm_error_payload(exc),
                    **correlation,
                    **publication.event_fields(final=True),
                },
                status="failed",
                service_port=service_port,
            )
    publication = (
        trace_publication()
        if trace_publication
        else TraceArtifactPublication(complete=False)
    )
    await _emit_llm_call_event(
        telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        event_type="llm.call.completed",
        payload={
            "llm_call_id": llm_call_id,
            "provider_name": provider_name,
            "service_vendor": service_vendor or provider_name,
            "response_model": str(getattr(response, "model", "") or ""),
            "provider_round_trip_ms": (time.monotonic() - started_at) * 1000,
            "usage": usage_payload_from_response_usage(
                getattr(response, "usage", None)
            ),
            "finish_reason": str(getattr(response, "finish_reason", "") or ""),
            **correlation,
            **publication.event_fields(final=True),
        },
        status="completed",
        service_port=service_port,
    )
    return response


async def generate_with_provider_trace_telemetry(
    *,
    service_port: Any,
    request: ProviderRequest,
    session_id: str,
    turn_id: str,
    trace_args: dict[str, Any],
) -> ProviderResponse:
    provider_trace_args = {
        key: value for key, value in trace_args.items() if key != "service_vendor"
    }
    publication = trace_provider_request(
        provider_request=request, **provider_trace_args
    )

    async def generate() -> ProviderResponse:
        nonlocal publication
        response = await service_port.generate_normalized(request)
        publication = publication.merge(
            trace_provider_response(provider_response=response, **provider_trace_args)
        )
        return response

    return await generate_with_provider_call_telemetry(
        service_port=service_port,
        request=request,
        session_id=session_id,
        turn_id=turn_id,
        provider_name=str(trace_args["provider_name"]),
        service_vendor=str(trace_args.get("service_vendor") or ""),
        generate=generate,
        trace_publication=lambda: publication,
    )
