import json
from pathlib import Path
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_TRACE_REQUESTS_ENV
from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.telemetry.constants import TRACE_HOME_ROOT_METADATA_KEY
from openminion.modules.telemetry.trace.structured import trace_context_payload
from openminion.modules.telemetry.trace.layout import (
    build_trace_file_path,
    resolve_trace_root,
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
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        logger.debug("trace_request: wrote %s", trace_path)
        raw_text = _provider_request_raw_text(provider_request)
        if raw_text:
            raw_path.write_text(raw_text + "\n", encoding="utf-8")
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
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        write_structured_trace(
            trace_context=trace_context,
            patch={
                "response": {
                    "ok": payload["ok"],
                    "finish_reason": payload["finish_reason"],
                    "output_text": payload["output_text"],
                    "tool_calls": payload["tool_calls"],
                    "thinking_blocks": payload["thinking_blocks"],
                    "error": payload["error"],
                }
            },
        )
        logger.debug("trace_response: wrote %s", trace_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace_response: failed to write trace: %s", exc)


def merge_metadata(
    metadata: dict[str, str],
    *,
    model: str | None,
    provider_name: str,
    inference_steps: int,
    untrusted_metadata: dict[str, str],
    untrusted_events: list[dict[str, str]],
    self_improvement_metadata: dict[str, str],
) -> dict[str, str]:
    merged = dict(metadata)
    # Keep tool resolution metadata contract stable on all paths.
    merged.setdefault("model_tool_name", "")
    merged.setdefault("runtime_binding_id", "")
    merged.setdefault("runtime_tool_name", "")
    merged.setdefault("runtime_fallback_chain", "[]")
    merged.setdefault("runtime_fallback_used", "false")
    merged.setdefault("runtime_resolution_source", "")
    if model and not merged.get("model"):
        merged["model"] = str(model)
    merged.setdefault("provider", provider_name)
    merged["inference_steps"] = str(inference_steps)
    for key, value in untrusted_metadata.items():
        merged[key] = value
    for key, value in self_improvement_metadata.items():
        merged[key] = value
    events: list[dict[str, str]] = []
    raw_events = str(merged.get("security_events", "")).strip()
    if raw_events:
        try:
            parsed = json.loads(raw_events)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    events.append({str(k): str(v) for k, v in item.items()})
    if untrusted_events:
        events.extend(untrusted_events)
    if events:
        merged["security_events"] = json.dumps(events, sort_keys=True)
    return merged
