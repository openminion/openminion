"""Support helpers for the LLM runtime client."""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError

from ..constants import (
    LLM_TOOL_CALL_STATUS_BLOCKED,
    LLM_TOOL_CALL_STATUS_ERROR,
    LLM_TOOL_CALL_STATUS_PARSED,
    LLM_TOOL_CHOICE_NONE,
)
from ..config import RoutingPolicy, resolve_provider_config
from ..diagnostics.events import emit_llm_operation as _emit_llm_operation
from ..errors import ErrorCode, LLMCtlError
from ..providers.cost import estimate_usage_cost_usd
from ..schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ResponseError,
    ToolCall,
    UsageInfo,
)

RETRYABLE_CODES = {"RATE_LIMITED", "TIMEOUT", "PROVIDER_ERROR"}
LOGGER = logging.getLogger(__name__)


@dataclass
class ToolPolicyContext:
    enabled: bool
    allowed_tools: set[str]
    block_on_disallowed_tool_call: bool


def _estimate_input_tokens(messages: Iterable[Message]) -> int:
    chars = sum(len(msg.content) for msg in messages)
    if chars <= 0:
        return 0
    return max(1, chars // 4)


def _redact_text(value: str, mode: str) -> str:
    if mode == "off":
        return value
    import re

    redacted = value
    patterns_normal = [
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s]+)",
        r"(?i)bearer\s+[a-z0-9\.\-_]+",
    ]
    patterns_strict = patterns_normal + [r"\b[a-zA-Z0-9_-]{24,}\b"]
    patterns = patterns_strict if mode == "strict" else patterns_normal
    for expr in patterns:
        redacted = re.sub(expr, "[REDACTED]", redacted)
    return redacted


def _redact_obj(obj: Any, mode: str) -> Any:
    if mode == "off":
        return obj
    if isinstance(obj, str):
        return _redact_text(obj, mode)
    if isinstance(obj, list):
        return [_redact_obj(item, mode) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact_obj(val, mode) for key, val in obj.items()}
    return obj


def _safe_code(code: str) -> ErrorCode:
    allowed = {
        "INVALID_ARGUMENT",
        "AUTH_ERROR",
        "RATE_LIMITED",
        "TIMEOUT",
        "PROVIDER_ERROR",
        "POLICY_DENIED",
        "INTERNAL_ERROR",
        "EMPTY_PAYLOAD",
        "EMPTY_URN_CONTENT",
        "MALFORMED_PAYLOAD",
    }
    return code if code in allowed else "INTERNAL_ERROR"


def _resolve_provider_and_model(
    client: Any,
    request: LLMRequest,
    overrides: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[RoutingPolicy]]:
    explicit_provider = overrides.get("provider", request.provider)
    explicit_model = overrides.get("model", request.model)

    if explicit_provider:
        provider_name = str(explicit_provider)
    else:
        provider_name = (
            client.profile.default_provider
            or client.llmctl.config.llmctl.default_provider
        )

    if explicit_model:
        model_name = str(explicit_model)
    else:
        model_name = (
            client.profile.default_model or client.llmctl.config.llmctl.default_model
        )

    routing = client.profile.routing or client.llmctl.config.llmctl.routing_defaults
    if not explicit_provider and not explicit_model and routing and routing.primary:
        provider_name = routing.primary.provider
        model_name = routing.primary.model

    return provider_name, model_name, routing


def _merge_generation_params(
    client: Any,
    request: LLMRequest,
    overrides: Dict[str, Any],
) -> LLMRequest:
    global_defaults = client.llmctl.config.llmctl.generation_defaults
    agent_defaults = client.profile.generation_defaults

    merged: Dict[str, Any] = {
        "temperature": request.temperature,
        "top_p": request.top_p,
        "max_output_tokens": request.max_output_tokens,
        "stop": request.stop,
        "stream": request.stream,
    }

    for key in ("temperature", "top_p", "max_output_tokens", "stop"):
        if getattr(global_defaults, key) is not None:
            merged[key] = getattr(global_defaults, key)
        if getattr(agent_defaults, key) is not None:
            merged[key] = getattr(agent_defaults, key)
        if getattr(request, key) is not None:
            merged[key] = getattr(request, key)
        if key in overrides and overrides[key] is not None:
            merged[key] = overrides[key]

    if "stream" in overrides:
        merged["stream"] = bool(overrides["stream"])

    return request.model_copy(update=merged)


def _apply_budgets(
    client: Any, request: LLMRequest
) -> Tuple[Optional[LLMResponse], LLMRequest]:
    budgets = client.profile.budgets
    metadata = dict(request.metadata)

    if budgets.max_output_tokens is not None:
        if request.max_output_tokens is None:
            request = request.model_copy(
                update={"max_output_tokens": budgets.max_output_tokens}
            )
        else:
            capped = min(request.max_output_tokens, budgets.max_output_tokens)
            request = request.model_copy(update={"max_output_tokens": capped})

    input_estimate = _estimate_input_tokens(request.messages)
    metadata["input_tokens_estimate"] = input_estimate

    if (
        budgets.max_input_tokens is not None
        and input_estimate > budgets.max_input_tokens
    ):
        action = (
            budgets.soft_input_cap_action
            or client.llmctl.config.llmctl.budgets.soft_input_cap_action
        )
        metadata["input_soft_cap_exceeded"] = True
        metadata["input_soft_cap_action"] = action
        request = request.model_copy(update={"metadata": metadata})

        if action == LLM_TOOL_CALL_STATUS_ERROR:
            return (
                client._error_response(
                    provider=request.provider or "",
                    model=request.model or "",
                    code="POLICY_DENIED",
                    message="Input token estimate exceeded max_input_tokens soft cap",
                    details={
                        "estimated_input_tokens": input_estimate,
                        "max_input_tokens": budgets.max_input_tokens,
                    },
                ),
                request,
            )

    request = request.model_copy(update={"metadata": metadata})
    return None, request


def _apply_tool_policy_pre(
    client: Any,
    request: LLMRequest,
) -> Tuple[LLMRequest, ToolPolicyContext]:
    policy = client.profile.tool_policy
    tools = list(request.tools or [])

    if not policy.enable_tools:
        stripped = request.model_copy(
            update={"tools": [], "tool_choice": LLM_TOOL_CHOICE_NONE}
        )
        return (
            stripped,
            ToolPolicyContext(
                enabled=False,
                allowed_tools=set(),
                block_on_disallowed_tool_call=policy.block_on_disallowed_tool_call,
            ),
        )

    if policy.allowed_tools is not None:
        allowset = set(policy.allowed_tools)
        filtered = [tool for tool in tools if tool.name in allowset]
        request = request.model_copy(update={"tools": filtered})
        allowed_tools = {tool.name for tool in filtered}
    else:
        allowed_tools = {tool.name for tool in tools}

    tool_choice = request.tool_choice
    if tool_choice is None and policy.tool_choice_default is not None:
        tool_choice = policy.tool_choice_default
    request = request.model_copy(update={"tool_choice": tool_choice})

    return (
        request,
        ToolPolicyContext(
            enabled=True,
            allowed_tools=allowed_tools,
            block_on_disallowed_tool_call=policy.block_on_disallowed_tool_call,
        ),
    )


def _retry_config(client: Any, overrides: Dict[str, Any]) -> Tuple[int, int]:
    global_retry = client.llmctl.config.llmctl.retries
    profile_retry = client.profile.retries
    max_retries = global_retry.max_retries
    backoff_ms = global_retry.backoff_ms

    if profile_retry.max_retries is not None:
        max_retries = profile_retry.max_retries
    if profile_retry.backoff_ms is not None:
        backoff_ms = profile_retry.backoff_ms
    if overrides.get("max_retries") is not None:
        max_retries = int(overrides["max_retries"])
    if overrides.get("backoff_ms") is not None:
        backoff_ms = int(overrides["backoff_ms"])

    return max(0, int(max_retries)), max(0, int(backoff_ms))


def _emit_operation(
    client: Any,
    *,
    request: LLMRequest,
    operation: str,
    provider_name: str,
    model_name: str,
    status: str = "ok",
    attempt: int | None = None,
    error_code: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> bool:
    if client._telemetryctl is None:
        return False

    metadata = dict(request.metadata or {})
    session_id = str(metadata.get("session_id", "")).strip()
    turn_id = str(metadata.get("turn_id", "")).strip()
    if not session_id or not turn_id:
        return False

    payload_extra: Dict[str, Any] = {}
    trace_id = str(metadata.get("trace_id", "")).strip()
    if trace_id:
        payload_extra["trace_id"] = trace_id
    mode_name = str(metadata.get("mode_name", "")).strip().lower()
    if mode_name:
        payload_extra["mode"] = mode_name
    if extra:
        payload_extra.update(extra)

    return _emit_llm_operation(
        telemetryctl=client._telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        operation=operation,
        provider=provider_name,
        model=model_name,
        status=status,
        attempt=attempt,
        error_code=error_code,
        extra=payload_extra or None,
    )


def _cache_hit_payload(response: LLMResponse) -> Dict[str, Any] | None:
    telemetry = response.telemetry if isinstance(response.telemetry, dict) else {}
    if telemetry:
        cache_hit = telemetry.get("cache_hit")
        if isinstance(cache_hit, bool) and cache_hit:
            return {"cache_hit": True}
        cached_tokens = telemetry.get("cached_tokens")
        try:
            cached_value = float(cached_tokens)
        except (TypeError, ValueError):
            cached_value = 0.0
        if cached_value > 0:
            return {"cache_hit": True, "cached_tokens": cached_value}
    return None


def _execute_with_routing(
    client: Any,
    request: LLMRequest,
    provider_name: str,
    model_name: str,
    routing: Optional[RoutingPolicy],
    overrides: Dict[str, Any],
) -> LLMResponse:
    response = client._call_with_retries(
        provider_name,
        model_name,
        request,
        overrides,
    )
    if response.ok or not routing:
        return response

    error_code = response.error.code if response.error is not None else "PROVIDER_ERROR"
    for fallback in routing.fallbacks:
        if fallback.on and error_code not in fallback.on:
            continue
        response = client._call_with_retries(
            fallback.provider,
            fallback.model,
            request,
            overrides,
        )
        if response.ok:
            return response
        error_code = (
            response.error.code if response.error is not None else "PROVIDER_ERROR"
        )
    return response


def _call_with_retries(
    client: Any,
    provider_name: str,
    model_name: str,
    request: LLMRequest,
    overrides: Dict[str, Any],
) -> LLMResponse:
    max_retries, backoff_ms = client._retry_config(overrides)
    last_response: Optional[LLMResponse] = None

    for attempt in range(max_retries + 1):
        started = time.perf_counter()
        try:
            provider = client.llmctl.registry.get(provider_name)
        except KeyError:
            return client._error_response(
                provider=provider_name,
                model=model_name,
                code="INVALID_ARGUMENT",
                message=f"Unknown provider: {provider_name}",
            )

        call_request = request.model_copy(
            update={"provider": provider_name, "model": model_name}
        )
        cfg = resolve_provider_config(client.llmctl.config, provider_name)
        cfg["timeouts"] = client.llmctl.config.llmctl.timeouts.model_dump()

        client._emit_operation(
            request=call_request,
            operation="request",
            provider_name=provider_name,
            model_name=model_name,
            attempt=attempt + 1,
        )

        try:
            raw_resp = provider.complete(call_request, cfg)
            response = client._normalize_response(
                raw_resp,
                provider_name,
                model_name,
                allowed_tool_names={
                    str(getattr(tool, "name", "") or "").strip()
                    for tool in call_request.tools or []
                    if str(getattr(tool, "name", "") or "").strip()
                },
            )
        except LLMCtlError as exc:
            response = client._error_response(
                provider=provider_name,
                model=model_name,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        except Exception as exc:
            response = client._error_response(
                provider=provider_name,
                model=model_name,
                code="PROVIDER_ERROR",
                message=f"{type(exc).__name__}: {exc}",
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.latency_ms <= 0:
            response = response.model_copy(update={"latency_ms": elapsed_ms})

        if response.ok:
            client._emit_operation(
                request=call_request,
                operation="response",
                provider_name=provider_name,
                model_name=model_name,
                attempt=attempt + 1,
                extra={"latency_ms": response.latency_ms},
            )
            cache_hit = client._cache_hit_payload(response)
            if cache_hit is not None:
                client._emit_operation(
                    request=call_request,
                    operation="cache_hit",
                    provider_name=provider_name,
                    model_name=model_name,
                    attempt=attempt + 1,
                    extra=cache_hit,
                )
            return response

        last_response = response
        err_code = (
            response.error.code if response.error is not None else "PROVIDER_ERROR"
        )
        client._emit_operation(
            request=call_request,
            operation="error",
            provider_name=provider_name,
            model_name=model_name,
            status="error",
            attempt=attempt + 1,
            error_code=err_code,
            extra={"latency_ms": response.latency_ms},
        )
        if err_code in RETRYABLE_CODES and attempt < max_retries:
            client._emit_operation(
                request=call_request,
                operation="retry",
                provider_name=provider_name,
                model_name=model_name,
                attempt=attempt + 1,
                error_code=err_code,
            )
            time.sleep(max(0.0, (backoff_ms / 1000.0) * (2**attempt)))
            continue
        return response

    if last_response is not None:
        return last_response
    return client._error_response(
        provider=provider_name,
        model=model_name,
        code="INTERNAL_ERROR",
        message="Provider call failed without response",
    )


def _apply_tool_policy_post(
    client: Any, response: LLMResponse, ctx: ToolPolicyContext
) -> LLMResponse:
    if not response.tool_calls:
        return response

    from ..providers.tool_calling import _resolve_allowed_tool_name

    blocked_any = False
    updated_calls: List[ToolCall] = []
    for call in response.tool_calls:
        call_name = str(call.name or "").strip()
        if call_name in ctx.allowed_tools:
            updated_calls.append(call)
            continue
        resolved_name = _resolve_allowed_tool_name(
            call_name,
            allowed_tool_names=ctx.allowed_tools,
        )
        if resolved_name:
            updated_calls.append(
                call.model_copy(
                    update={
                        "name": resolved_name,
                        "status": LLM_TOOL_CALL_STATUS_PARSED,
                    }
                )
            )
            continue
        blocked_any = True
        updated_calls.append(
            call.model_copy(
                update={
                    "status": LLM_TOOL_CALL_STATUS_BLOCKED,
                    "error": "Disallowed by tool policy",
                }
            )
        )

    response = response.model_copy(update={"tool_calls": updated_calls})
    if blocked_any and ctx.block_on_disallowed_tool_call:
        return client._error_response(
            provider=response.provider,
            model=response.model,
            code="POLICY_DENIED",
            message="Model emitted disallowed tool call",
            details={
                "blocked_tools": [
                    c.name
                    for c in updated_calls
                    if c.status == LLM_TOOL_CALL_STATUS_BLOCKED
                ]
            },
            tool_calls=updated_calls,
        )
    return response


def _apply_cost_budget(client: Any, response: LLMResponse) -> LLMResponse:
    max_cost = client.profile.budgets.max_cost_usd
    if not response.ok or max_cost is None:
        return response

    observed_cost = response.cost_usd
    evaluated_cost = observed_cost
    if evaluated_cost is None:
        evaluated_cost = estimate_usage_cost_usd(
            usage=response.usage,
            cost_hint=client._resolve_provider_cost_hint(response.provider),
        )
    if evaluated_cost is None:
        LOGGER.warning(
            "llm.cost_budget.unassessable provider=%s model=%s max_cost_usd=%s",
            response.provider or "",
            response.model or "",
            max_cost,
        )
        return response
    if evaluated_cost <= max_cost:
        return response

    details: Dict[str, Any] = {
        "max_cost_usd": max_cost,
        "cost_source": "provider" if observed_cost is not None else "estimated",
    }
    if observed_cost is not None:
        details["cost_usd"] = observed_cost
    else:
        details["estimated_cost_usd"] = evaluated_cost

    return client._error_response(
        provider=response.provider,
        model=response.model,
        code="POLICY_DENIED",
        message="Response cost exceeded max_cost_usd budget",
        details=details,
        tool_calls=response.tool_calls,
    )


def _resolve_provider_cost_hint(client: Any, provider_name: str) -> Any:
    resolved_name = str(provider_name or client.profile.default_provider or "").strip()
    if not resolved_name:
        return None
    provider_cfg = client.llmctl.config.providers.get(resolved_name)
    return None if provider_cfg is None else provider_cfg.cost_hint


def _finalize_response(client: Any, response: LLMResponse) -> LLMResponse:
    include_raw = client.llmctl.config.llmctl.logging.include_provider_raw
    if client.profile.logging.include_provider_raw is not None:
        include_raw = client.profile.logging.include_provider_raw

    mode = client.llmctl.config.llmctl.logging.redaction
    redacted_output = _redact_text(response.output_text, mode)
    redacted_assistant = [
        msg.model_copy(update={"content": _redact_text(msg.content, mode)})
        if msg.role == "assistant"
        else msg
        for msg in response.assistant_messages
    ]

    error = response.error
    if error is not None:
        error = error.model_copy(
            update={
                "message": _redact_text(error.message, mode),
                "details": _redact_obj(error.details, mode),
            }
        )

    provider_raw = response.provider_raw if include_raw else None
    if provider_raw is not None:
        provider_raw = _redact_obj(provider_raw, mode)

    return response.model_copy(
        update={
            "output_text": redacted_output,
            "assistant_messages": redacted_assistant,
            "error": error,
            "provider_raw": provider_raw,
        }
    )


def _error_response(
    _client: Any,
    *,
    provider: str,
    model: str,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    tool_calls: Optional[List[ToolCall]] = None,
) -> LLMResponse:
    safe = _safe_code(code)
    return LLMResponse(
        ok=False,
        provider=provider,
        model=model,
        output_text="",
        assistant_messages=[],
        tool_calls=tool_calls or [],
        usage=UsageInfo(input_tokens=0, output_tokens=0, total_tokens=0),
        latency_ms=0,
        finish_reason="error",
        provider_raw=None,
        error=ResponseError(code=safe, message=message, details=details or {}),
    )


def parse_call_payload(payload: Optional[str]) -> Tuple[LLMRequest, Dict[str, Any]]:
    raw = payload
    if raw is None:
        raise LLMCtlError("INVALID_ARGUMENT", "Empty call payload")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMCtlError("INVALID_ARGUMENT", f"Invalid JSON payload: {exc}") from exc

    if not isinstance(parsed, dict):
        raise LLMCtlError("INVALID_ARGUMENT", "Call payload must be an object")

    request_obj = parsed.get("request", parsed)
    overrides = parsed.get("overrides", {})
    if not isinstance(overrides, dict):
        raise LLMCtlError("INVALID_ARGUMENT", "overrides must be an object")

    try:
        request = LLMRequest.model_validate(request_obj)
    except ValidationError as exc:
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            "Call request schema validation failed",
            {"errors": exc.errors()},
        ) from exc

    return request, overrides


__all__ = [
    "LOGGER",
    "RETRYABLE_CODES",
    "ToolPolicyContext",
    "_apply_budgets",
    "_apply_cost_budget",
    "_apply_tool_policy_post",
    "_apply_tool_policy_pre",
    "_cache_hit_payload",
    "_call_with_retries",
    "_emit_operation",
    "_error_response",
    "_estimate_input_tokens",
    "_execute_with_routing",
    "_finalize_response",
    "_merge_generation_params",
    "_redact_obj",
    "_redact_text",
    "_resolve_provider_and_model",
    "_resolve_provider_cost_hint",
    "_retry_config",
    "_safe_code",
    "parse_call_payload",
]
