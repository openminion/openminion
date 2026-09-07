import json
import time
from collections.abc import Iterable, Iterator
from typing import Any

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...constants import (
    LLM_TOOL_CALL_STATUS_PARSED,
    LLM_TOOL_CALL_STRATEGY_HYBRID,
    LLM_TOOL_CHOICE_AUTO,
    LLM_TOOL_CHOICE_REQUIRED,
)
from ...errors import LLMCtlError
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    Message,
    ResponseError,
    ToolCall,
)
from ..transport.client import ProviderHTTPClient, http_client_for_config
from ..transport.sse import iter_sse_post_lines
from ..contract import PROVIDER_INTERFACE_VERSION
from ..message_payloads import (
    _collapse_system_messages,
    _extract_message_text,
    _extract_openai_like_thinking_blocks,
    _extract_openai_like_primary_text,
    _coerce_tool_calls,
    _http_json_get,
    _http_json_post,
    _list_models_from_config,
    _messages_openai_like,
    _resolve_api_key,
    _resolve_model,
    _resolve_timeout_seconds,
    _resolve_tool_names,
    _usage_from_openai_like,
)
from ..tool_choice import should_retry_with_auto_tool_choice
from ..tool_calling import (
    build_tool_schema_name_map,
    build_openai_tools_payload,
    normalize_tool_choice,
    remap_provider_tool_call_name,
    resolve_tool_call_source_precedence,
    supports_fallback_tool_calling,
    supports_native_tool_calling,
    ToolCallFallbackSource,
)
from ..behavior import resolve_behavior_profile
from .request_compatibility import (
    requires_auto_tool_choice_compat,
    resolve_openai_request_compat,
    should_retry_tool_transcript_error,
)


def _append_retry_system_instruction(
    messages: list[dict[str, Any]],
    instruction: str,
    *,
    collapse_system_messages: bool = False,
) -> list[dict[str, Any]]:
    note = str(instruction or "").strip()
    if not note:
        return list(messages)
    result = [dict(item) for item in messages]
    result.append({"role": "system", "content": note})
    if collapse_system_messages:
        return _collapse_system_messages(result)
    return result


def _empty_payload_retry_instruction(
    base_instruction: str,
    *,
    message_payload: dict[str, Any],
    allowed_tool_names: list[str],
) -> str:
    raw_tool_calls = message_payload.get("tool_calls")
    if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
        return base_instruction
    visible_tools = ", ".join(sorted(allowed_tool_names))
    instruction = (
        f"{base_instruction} The previous native tool calls were not currently "
        f"available. Use only these visible tools: {visible_tools}."
    )
    if "tool.request" in allowed_tool_names:
        instruction += (
            " If you need an inactive tool, first call the visible "
            "tool.request (provider-safe tool_request) control with its exact "
            "canonical name."
        )
    return instruction


def _apply_empty_payload_retry(
    request: LLMRequest,
    request_compat: Any,
    message_payload: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    instruction = _empty_payload_retry_instruction(
        request_compat.empty_payload_retry_instruction,
        message_payload=message_payload,
        allowed_tool_names=_resolve_tool_names(request),
    )
    payload["messages"] = _append_retry_system_instruction(
        payload.get("messages", []),
        instruction,
        collapse_system_messages=request_compat.collapse_system_messages,
    )


def _retry_2013(
    request_compat: Any,
    retry_used: bool,
    payload: dict[str, Any],
    error: LLMCtlError,
) -> bool:
    return bool(
        request_compat.retry_tool_transcript_error_once
        and not retry_used
        and any(
            item.get("role") == "tool"
            for item in payload.get("messages", [])
            if isinstance(item, dict)
        )
        and should_retry_tool_transcript_error(error)
    )


def _openai_tool_choice(
    request: LLMRequest,
    *,
    request_compat: Any,
    tool_name_map: Any,
) -> Any:
    canonical_to_external = (
        tool_name_map.canonical_to_external if tool_name_map else None
    )
    normalized = normalize_tool_choice(
        request.tool_choice,
        canonical_to_external=canonical_to_external,
    )
    if (
        normalized == LLM_TOOL_CHOICE_REQUIRED
        and request_compat.force_single_required_tool
        and request.tools
        and len(request.tools) == 1
    ):
        return normalize_tool_choice(
            {"name": request.tools[0].name},
            canonical_to_external=canonical_to_external,
        )
    return normalized


def _openai_stream_payload(
    request: LLMRequest,
    *,
    model: str,
    request_compat: Any,
    tool_call_strategy: str,
    tool_name_map: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _messages_openai_like(
            request,
            include_fallback_instruction=False,
            collapse_system_messages=request_compat.collapse_system_messages,
            extra_system_instruction=request_compat.native_tool_only_instruction,
            tool_name_overrides=(
                tool_name_map.canonical_to_external if tool_name_map else None
            ),
            preserve_tool_call_raw_arguments=request_compat.preserve_tool_arguments,
        ),
        "stream": True,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_output_tokens is not None and request.max_output_tokens > 0:
        payload["max_tokens"] = request.max_output_tokens
    if request.stop:
        payload["stop"] = request.stop
    if (
        request.tools
        and request_compat.include_stream_tool_contract
        and supports_native_tool_calling(tool_call_strategy)
    ):
        payload["tools"] = build_openai_tools_payload(
            request.tools,
            canonical_to_external=(
                tool_name_map.canonical_to_external if tool_name_map else None
            ),
        )
        payload["tool_choice"] = _openai_tool_choice(
            request,
            request_compat=request_compat,
            tool_name_map=tool_name_map,
        )
    return payload


def _merge_openai_stream_tool_call_delta(
    states: dict[int, dict[str, str]],
    raw_delta: dict[str, Any],
) -> None:
    try:
        index = int(raw_delta.get("index", 0))
    except (TypeError, ValueError) as exc:
        raise LLMCtlError(
            "PROVIDER_ERROR", "openai stream tool call delta has invalid index"
        ) from exc
    state = states.setdefault(index, {"id": "", "name": "", "arguments": ""})
    state["id"] += str(raw_delta.get("id") or "")
    function_payload = raw_delta.get("function")
    if isinstance(function_payload, dict):
        state["name"] += str(function_payload.get("name") or "")
        state["arguments"] += str(function_payload.get("arguments") or "")


def _reconstruct_openai_stream_tool_calls(
    states: dict[int, dict[str, str]],
) -> list[ToolCall]:
    tool_calls: list[ToolCall] = []
    for index in sorted(states):
        state = states[index]
        name = state["name"]
        raw_arguments = state["arguments"]
        if not name:
            raise LLMCtlError(
                "PROVIDER_ERROR", "openai stream tool call is missing function name"
            )
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            raise LLMCtlError(
                "PROVIDER_ERROR",
                "openai stream tool call has malformed arguments",
                details={"tool_call_id": state["id"], "tool_call_index": index},
            ) from exc
        if not isinstance(arguments, dict):
            raise LLMCtlError(
                "PROVIDER_ERROR",
                "openai stream tool call arguments must be an object",
                details={"tool_call_id": state["id"], "tool_call_index": index},
            )
        tool_calls.append(
            ToolCall(
                id=state["id"] or None,
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments,
                status=LLM_TOOL_CALL_STATUS_PARSED,
                batch_index=index,
            )
        )
    return tool_calls


def _portal_native_tool_calls(
    message_payload: dict[str, Any],
    *,
    allowed_tool_names: list[str],
    tool_name_map: Any,
) -> list[ToolCall]:
    raw_calls = message_payload.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    allowed = set(allowed_tool_names)
    tool_calls: list[ToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise LLMCtlError(
                "PROVIDER_ERROR", "openai response has malformed tool call"
            )
        function_payload = raw_call.get("function")
        if not isinstance(function_payload, dict):
            raise LLMCtlError(
                "PROVIDER_ERROR", "openai response tool call is missing function"
            )
        provider_name = str(function_payload.get("name") or "").strip()
        name = remap_provider_tool_call_name(
            provider_name,
            external_to_canonical=(
                tool_name_map.external_to_canonical if tool_name_map else None
            ),
        )
        if not name or name not in allowed:
            raise LLMCtlError(
                "PROVIDER_ERROR",
                "openai response returned an unsupported tool",
                details={
                    "tool_name": provider_name,
                    "tool_call_id": str(raw_call.get("id") or ""),
                    "tool_call_index": index,
                    "allowed_tool_names": sorted(allowed),
                },
            )
        raw_arguments_value = function_payload.get("arguments", "")
        if isinstance(raw_arguments_value, str):
            raw_arguments = raw_arguments_value
            try:
                arguments = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError as exc:
                raise LLMCtlError(
                    "PROVIDER_ERROR",
                    "openai response tool call has malformed arguments",
                    details={
                        "tool_call_id": str(raw_call.get("id") or ""),
                        "tool_call_index": index,
                    },
                ) from exc
        elif isinstance(raw_arguments_value, dict):
            arguments = dict(raw_arguments_value)
            raw_arguments = None
        else:
            arguments = None
            raw_arguments = None
        if not isinstance(arguments, dict):
            raise LLMCtlError(
                "PROVIDER_ERROR",
                "openai response tool call arguments must be an object",
                details={
                    "tool_call_id": str(raw_call.get("id") or ""),
                    "tool_call_index": index,
                },
            )
        tool_calls.append(
            ToolCall(
                id=str(raw_call.get("id") or "") or None,
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments,
                status=LLM_TOOL_CALL_STATUS_PARSED,
                batch_index=index,
            )
        )
    return tool_calls


def _resolve_openai_response_content(
    *,
    request: LLMRequest,
    response_payload: dict[str, Any],
    first_choice: dict[str, Any],
    message_payload: dict[str, Any],
    model: str,
    behavior_profile: Any,
    request_compat: Any,
    tool_name_map: Any,
) -> tuple[str, str, list[dict[str, Any]], list[ToolCall], Any]:
    allowed_tool_names = _resolve_tool_names(request)
    expanded_allowed_tool_names = (
        tool_name_map.expand_allowed_tool_names(allowed_tool_names)
        if tool_name_map is not None
        else allowed_tool_names
    )
    text, raw_text, text_source = _extract_openai_like_primary_text(
        response_payload=response_payload,
        first_choice=first_choice,
        message_payload=message_payload,
    )
    reasoning_text = _extract_message_text(message_payload.get("reasoning"))
    thinking_blocks = _extract_openai_like_thinking_blocks(message_payload)
    resolution = resolve_tool_call_source_precedence(
        message_payload=message_payload,
        fallback_sources=[
            ToolCallFallbackSource(source=text_source, text=raw_text),
            ToolCallFallbackSource(source="message.reasoning", text=reasoning_text),
        ],
        provider_name="openai",
        model_name=str(response_payload.get("model") or model),
        allowed_tool_names=(expanded_allowed_tool_names if request.tools else None),
        fallback_enabled=bool(
            request.tools and request_compat.enable_structured_tool_envelope_parse
        ),
        parser_plugin_selection=behavior_profile.parser_plugin_selection,
        fallback_parser_policy=behavior_profile.fallback_parser_policy,
        fallback_mode="structured",
    )
    if request_compat.preserve_tool_arguments:
        tool_calls = _portal_native_tool_calls(
            message_payload,
            allowed_tool_names=allowed_tool_names,
            tool_name_map=tool_name_map,
        )
    else:
        tool_calls = _coerce_tool_calls(
            [
                {
                    "id": getattr(call, "id", None),
                    "name": remap_provider_tool_call_name(
                        getattr(call, "name", ""),
                        external_to_canonical=(
                            tool_name_map.external_to_canonical
                            if tool_name_map
                            else None
                        ),
                    ),
                    "arguments": dict(getattr(call, "arguments", {}) or {}),
                    "status": LLM_TOOL_CALL_STATUS_PARSED,
                }
                for call in resolution.calls
            ]
        )
    if tool_calls and resolution.selected_source != "native":
        return "", resolution.selected_source, thinking_blocks, tool_calls, resolution
    return text, text_source, thinking_blocks, tool_calls, resolution


def _iter_openai_stream_events(
    lines: Iterable[str],
    *,
    response_metadata: dict[str, str],
    preserve_response_facts: bool,
) -> Iterator[LLMStreamEvent]:
    stream_event_type = "message"
    tool_call_states: dict[int, dict[str, str]] = {}
    finish_reason = ""
    usage = None
    try:
        for line in lines:
            if line.startswith("event:"):
                stream_event_type = line[len("event:") :].strip() or "message"
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:") :].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError as exc:
                yield LLMStreamEvent(
                    type="error",
                    error=ResponseError(
                        code="PROVIDER_ERROR",
                        message="openai stream malformed event payload",
                        details={
                            "stream_event_type": stream_event_type,
                            "error": str(exc),
                        },
                    ),
                )
                break
            choices = chunk.get("choices")
            first = (
                choices[0]
                if isinstance(choices, list)
                and choices
                and isinstance(choices[0], dict)
                else {}
            )
            delta = first.get("delta", {})
            content = delta.get("content") if isinstance(delta, dict) else None
            if not preserve_response_facts:
                if content:
                    yield LLMStreamEvent(type="delta", delta_text=str(content))
                continue
            raw_tool_call_deltas = (
                [
                    dict(item)
                    for item in delta.get("tool_calls", [])
                    if isinstance(item, dict)
                ]
                if isinstance(delta, dict) and isinstance(delta.get("tool_calls"), list)
                else []
            )
            for raw_delta in raw_tool_call_deltas:
                _merge_openai_stream_tool_call_delta(tool_call_states, raw_delta)
            raw_finish_reason = first.get("finish_reason")
            if raw_finish_reason is not None:
                finish_reason = str(raw_finish_reason)
            if isinstance(chunk.get("usage"), dict):
                usage = _usage_from_openai_like(chunk["usage"])
            yield LLMStreamEvent(
                type="delta",
                delta_text=str(content) if content is not None else None,
                tool_call_deltas=raw_tool_call_deltas,
                finish_reason=(
                    str(raw_finish_reason) if raw_finish_reason is not None else None
                ),
                usage=usage if isinstance(chunk.get("usage"), dict) else None,
                request_id=response_metadata.get("request_id") or None,
                provider_raw=chunk,
            )
        for tool_call in _reconstruct_openai_stream_tool_calls(tool_call_states):
            yield LLMStreamEvent(type="delta", tool_call=tool_call)
    except LLMCtlError as exc:
        yield LLMStreamEvent(
            type="error",
            error=ResponseError(
                code=exc.code,
                message=f"openai stream error: {exc.message}",
                details=dict(exc.details),
            ),
        )
    yield LLMStreamEvent(
        type="done",
        finish_reason=finish_reason or None,
        usage=usage,
        request_id=(response_metadata.get("request_id") or None)
        if preserve_response_facts
        else None,
    )


class OpenAIProvider:
    name = "openai"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION
    default_base_url = "https://api.openai.com/v1"

    def __init__(self) -> None:
        self._http_client = ProviderHTTPClient()

    def close(self) -> None:
        self._http_client.close()

    def _resolve_behavior_profile(
        self,
        *,
        model: str,
        base_url: str,
        provider_identity: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None,
        env: Any,
    ) -> Any:
        return resolve_behavior_profile(
            provider=self.name,
            model=model,
            base_url=base_url,
            provider_identity=provider_identity,
            metadata=metadata,
            env=env,
        )

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "gpt-4.1-mini")
        api_key = _resolve_api_key(config, self.name, required=True)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = self._resolve_behavior_profile(
            model=model,
            base_url=base_url,
            provider_identity=config.get("provider_identity"),
            metadata=request.metadata,
            env=config.get("__env__"),
        )
        request_compat = resolve_openai_request_compat(
            provider_identity=(
                behavior_profile.provider_identity.as_metadata()
                if behavior_profile.provider_identity is not None
                else None
            ),
            request_dialect=behavior_profile.request_dialect,
        )
        tool_call_strategy = str(
            config.get("tool_call_strategy", LLM_TOOL_CALL_STRATEGY_HYBRID)
        )
        tool_name_map = (
            build_tool_schema_name_map(
                request.tools,
                provider_name=self.name,
                model_name=model,
                capability=behavior_profile.tool_schema_capability,
            )
            if request.tools
            else None
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_openai_like(
                request,
                include_fallback_instruction=(
                    supports_fallback_tool_calling(tool_call_strategy)
                    and not request_compat.disable_fallback_instruction
                ),
                collapse_system_messages=request_compat.collapse_system_messages,
                extra_system_instruction=request_compat.native_tool_only_instruction,
                tool_name_overrides=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
                enable_vision_input=bool(config.get("enable_vision_input", False)),
                supports_vision_input=True,
                preserve_tool_call_raw_arguments=(
                    request_compat.preserve_tool_arguments
                ),
            ),
        }

        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.stop:
            payload["stop"] = request.stop

        if (
            request.tools
            and request_compat.include_native_tool_contract
            and supports_native_tool_calling(tool_call_strategy)
        ):
            payload["tools"] = build_openai_tools_payload(
                request.tools,
                canonical_to_external=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
            )
            normalized_tool_choice = _openai_tool_choice(
                request,
                request_compat=request_compat,
                tool_name_map=tool_name_map,
            )
            payload["tool_choice"] = normalized_tool_choice

        retry_override_id = ""
        compat_retry_allowed = (
            not request_compat.disable_adapter_retries
            and bool(request.tools)
            and requires_auto_tool_choice_compat(payload.get("tool_choice"))
        )
        empty_payload_retry_used = False
        transcript_retry = False
        response_metadata: dict[str, str] = {}
        http_kwargs = {
            "url": f"{base_url}/chat/completions",
            "payload": payload,
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "timeout_seconds": _resolve_timeout_seconds(
                config, metadata=request.metadata
            ),
            "provider_name": self.name,
            "trace_metadata": request.metadata,
            "env": config.get("__env__"),
            "http_client": http_client_for_config(self._http_client, config),
            "response_metadata": response_metadata,
            "allow_curl_fallback": not request_compat.disable_adapter_retries,
            "telemetryctl": config.get("telemetryctl"),
        }
        while True:
            try:
                response_payload = _http_json_post(**http_kwargs)
            except LLMCtlError as exc:
                if compat_retry_allowed and should_retry_with_auto_tool_choice(
                    exc, payload.get("tool_choice")
                ):
                    retry_override_id = "tool_choice_retry_to_auto"
                    payload["tool_choice"] = LLM_TOOL_CHOICE_AUTO
                    continue
                if _retry_2013(request_compat, transcript_retry, payload, exc):
                    transcript_retry = True
                    continue
                raise

            choices = response_payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise LLMCtlError(
                    "PROVIDER_ERROR", f"{self.name} response missing choices"
                )

            first_choice = choices[0] if isinstance(choices[0], dict) else None
            if not first_choice:
                raise LLMCtlError(
                    "PROVIDER_ERROR", f"{self.name} response has invalid choice payload"
                )

            message_payload = first_choice.get("message")
            if not isinstance(message_payload, dict):
                raise LLMCtlError(
                    "PROVIDER_ERROR", f"{self.name} response missing message payload"
                )

            try:
                (
                    text,
                    text_source,
                    thinking_blocks,
                    tool_calls,
                    tool_call_resolution,
                ) = _resolve_openai_response_content(
                    request=request,
                    response_payload=response_payload,
                    first_choice=first_choice,
                    message_payload=message_payload,
                    model=model,
                    behavior_profile=behavior_profile,
                    request_compat=request_compat,
                    tool_name_map=tool_name_map,
                )
            except LLMCtlError as exc:
                request_id = response_metadata.get("request_id")
                if request_id:
                    exc.details.setdefault("request_id", request_id)
                finish_reason = str(first_choice.get("finish_reason") or "").strip()
                if finish_reason:
                    exc.details.setdefault("finish_reason", finish_reason)
                raise

            if text or tool_calls:
                break

            if (
                request_compat.retry_empty_payload_once
                and not empty_payload_retry_used
                and request_compat.empty_payload_retry_instruction
            ):
                empty_payload_retry_used = True
                _apply_empty_payload_retry(
                    request, request_compat, message_payload, payload
                )
                continue

            raise LLMCtlError(
                "EMPTY_PAYLOAD",
                f"{self.name} response did not include text or tool calls",
                details={
                    "retryable": True,
                    "finish_reason": str(first_choice.get("finish_reason", "")).strip(),
                    "choice_keys": sorted(str(k) for k in first_choice.keys()),
                    "message_keys": sorted(str(k) for k in message_payload.keys()),
                    "response_keys": sorted(str(k) for k in response_payload.keys()),
                },
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        assistant_messages = [Message(role="assistant", content=text)] if text else []
        usage = _usage_from_openai_like(response_payload.get("usage"))
        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=str(response_payload.get("model") or model),
                output_text=text,
                assistant_messages=assistant_messages,
                tool_calls=tool_calls,
                thinking=thinking_blocks,
                usage=usage,
                latency_ms=elapsed_ms,
                finish_reason=str(first_choice.get("finish_reason", "")).strip(),
                provider_raw=response_payload,
                telemetry={"request_id": response_metadata["request_id"]}
                if response_metadata.get("request_id")
                else {},
                normalization_meta={
                    "adapter": "openai",
                    "behavior_profile_id": behavior_profile.profile_id,
                    **(
                        {
                            "provider_identity": (
                                behavior_profile.provider_identity.as_metadata()
                            )
                        }
                        if behavior_profile.provider_identity is not None
                        else {}
                    ),
                    **(
                        {
                            "provider.identity.heuristic_shadow": (
                                behavior_profile.heuristic_provider_identity.as_metadata()
                            )
                        }
                        if behavior_profile.heuristic_provider_identity is not None
                        else {}
                    ),
                    **(
                        {
                            "provider.identity.partial": {
                                "inferred_fields": list(
                                    behavior_profile.provider_identity_inferred_fields
                                )
                            }
                        }
                        if behavior_profile.provider_identity_inferred_fields
                        else {}
                    ),
                    **(
                        {
                            "provider.identity.heuristic_overridden": {
                                "overridden_fields": list(
                                    behavior_profile.provider_identity_overridden_fields
                                )
                            }
                        }
                        if behavior_profile.provider_identity_overridden_fields
                        else {}
                    ),
                    "request_compat_profile": request_compat.profile_id,
                    "request_dialect": behavior_profile.request_dialect,
                    **(
                        {"provider_retry_override": retry_override_id}
                        if retry_override_id
                        else {}
                    ),
                    **(
                        {"empty_payload_retry_used": True}
                        if empty_payload_retry_used
                        else {}
                    ),
                    "tool_call_strategy": tool_call_strategy,
                    "tool_choice_policy": behavior_profile.tool_choice_policy,
                    "text_source": text_source,
                    "tool_schema_capability": (
                        tool_name_map.capability.id if tool_name_map else "identity"
                    ),
                    "tool_schema_external_name_map": (
                        dict(tool_name_map.canonical_to_external)
                        if tool_name_map and tool_name_map.active
                        else {}
                    ),
                    **tool_call_resolution.as_metadata(),
                },
            )
        )

    def stream(
        self, request: LLMRequest, config: dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        model = _resolve_model(request, config, "gpt-4.1-mini")
        try:
            api_key = _resolve_api_key(config, self.name, required=True)
        except LLMCtlError as exc:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(code="AUTH_ERROR", message=str(exc)),
            )
            yield LLMStreamEvent(type="done")
            return

        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = self._resolve_behavior_profile(
            model=model,
            base_url=base_url,
            provider_identity=config.get("provider_identity"),
            metadata=request.metadata,
            env=config.get("__env__"),
        )
        request_compat = resolve_openai_request_compat(
            provider_identity=(
                behavior_profile.provider_identity.as_metadata()
                if behavior_profile.provider_identity is not None
                else None
            ),
            request_dialect=behavior_profile.request_dialect,
        )

        tool_call_strategy = str(
            config.get("tool_call_strategy", LLM_TOOL_CALL_STRATEGY_HYBRID)
        )
        tool_name_map = (
            build_tool_schema_name_map(
                request.tools,
                provider_name=self.name,
                model_name=model,
                capability=behavior_profile.tool_schema_capability,
            )
            if request.tools
            else None
        )

        payload = _openai_stream_payload(
            request,
            model=model,
            request_compat=request_compat,
            tool_call_strategy=tool_call_strategy,
            tool_name_map=tool_name_map,
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        timeout_seconds = _resolve_timeout_seconds(config, metadata=request.metadata)
        response_metadata: dict[str, str] = {}
        lines = iter_sse_post_lines(
            url=f"{base_url}/chat/completions",
            payload=payload,
            headers=headers,
            timeout_seconds=timeout_seconds,
            provider_name=self.name,
            trace_metadata=request.metadata,
            http_client=http_client_for_config(self._http_client, config),
            response_metadata=response_metadata,
            telemetryctl=config.get("telemetryctl"),
        )
        yield from _iter_openai_stream_events(
            lines,
            response_metadata=response_metadata,
            preserve_response_facts=request_compat.include_stream_tool_contract,
        )

    def list_models(self, config: dict[str, Any]) -> list[str]:
        request_compat = resolve_openai_request_compat(
            provider_identity=config.get("provider_identity")
        )
        if request_compat.profile_id != "cortensor_portal":
            return _list_models_from_config(config)
        api_key = _resolve_api_key(config, self.name, required=True)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        response_payload = _http_json_get(
            url=f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_seconds=_resolve_timeout_seconds(config),
            provider_name=self.name,
            env=config.get("__env__"),
            http_client=http_client_for_config(self._http_client, config),
            telemetryctl=config.get("telemetryctl"),
        )
        data = response_payload.get("data")
        if not isinstance(data, list):
            raise LLMCtlError("PROVIDER_ERROR", "openai response missing model data")
        return [
            str(item.get("id") or "").strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


def openai_provider() -> OpenAIProvider:
    return OpenAIProvider()
