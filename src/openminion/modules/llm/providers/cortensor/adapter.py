import json
import random
import time
import uuid
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...errors import LLMCtlError
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import LLMRequest, LLMResponse, Message, ToolCall
from ..behavior import resolve_behavior_profile
from ..contract import PROVIDER_INTERFACE_VERSION
from ..message_payloads import (
    _as_float,
    _as_int,
    _extract_message_text,
    _coerce_tool_calls,
    _http_json_post,
    _list_models_from_config,
    _messages_openai_like,
    _resolve_api_key,
    _resolve_model,
    _resolve_tool_names,
    _usage_from_openai_like,
)
from ..tool_calling import (
    build_fallback_tool_call_instruction,
    build_openai_tools_payload,
    normalize_tool_choice,
    resolve_tool_call_source_precedence,
    supports_fallback_tool_calling,
    supports_native_tool_calling,
    ToolCallFallbackSource,
)


class CortensorProvider:
    name = "cortensor"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION
    _DEFAULT_MAX_TOKENS_FLOOR = 4096
    _MIN_TIMEOUT_HEADROOM_SECONDS = 60
    _DEFAULT_BASE_URL = "http://127.0.0.1:8080/api/v2/completions"
    _OFFCHAIN_RESULT_MIN_WAIT_ATTEMPTS = 6
    _DEFAULT_EMPTY_RESULT_MAX_ATTEMPTS = 3
    _DEFAULT_EMPTY_RESULT_BACKOFF_MS = 500
    _DEFAULT_EMPTY_RESULT_MAX_BACKOFF_MS = 4000

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "gpt-oss-20b")
        base_url = self._normalize_cortensor_base_url(
            str(config.get("base_url") or "").strip() or self._DEFAULT_BASE_URL
        )
        behavior_profile = resolve_behavior_profile(
            provider=self.name,
            model=model,
            base_url=base_url,
            metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )
        configured_api_mode = str(config.get("api_mode", "auto"))
        api_mode = self._resolve_api_mode(
            configured_mode=configured_api_mode, base_url=base_url
        )
        tool_call_strategy = str(config.get("tool_call_strategy", "hybrid"))

        empty_result_max_attempts = _as_int(
            config.get("empty_result_max_attempts"),
            self._DEFAULT_EMPTY_RESULT_MAX_ATTEMPTS,
        )
        empty_result_backoff_ms = _as_int(
            config.get("empty_result_backoff_ms"), self._DEFAULT_EMPTY_RESULT_BACKOFF_MS
        )
        empty_result_max_backoff_ms = _as_int(
            config.get("empty_result_max_backoff_ms"),
            self._DEFAULT_EMPTY_RESULT_MAX_BACKOFF_MS,
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "openminion.modules.llm/provider_adapters",
        }
        api_key = _resolve_api_key(config, self.name, required=False)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        attempt = 0
        last_error: LLMCtlError | None = None
        parsed: dict[str, Any] | None = None
        response_payload: dict[str, Any] | None = None
        telemetry_attempt = 0
        telemetry_urn_present = False
        telemetry_empty_reason = ""
        while attempt < empty_result_max_attempts:
            attempt += 1
            try:
                if api_mode == "cortensor_completion":
                    response_payload = self._complete_via_completion_mode(
                        request=request,
                        config=config,
                        model=model,
                        headers=headers,
                        tool_call_strategy=tool_call_strategy,
                        base_url=base_url,
                    )
                else:
                    response_payload = self._complete_via_openai_chat_mode(
                        request=request,
                        config=config,
                        model=model,
                        headers=headers,
                        tool_call_strategy=tool_call_strategy,
                        base_url=base_url,
                    )

                parsed = self._parse_response_payload(
                    response_payload=response_payload,
                    request=request,
                    tool_call_strategy=tool_call_strategy,
                    fallback_model=model,
                    behavior_profile=behavior_profile,
                )
                break
            except LLMCtlError as exc:
                if (
                    exc.code in {"EMPTY_PAYLOAD", "EMPTY_URN_CONTENT"}
                    and attempt < empty_result_max_attempts
                ):
                    should_retry = api_mode == "openai_chat"
                    if should_retry:
                        last_error = exc
                        telemetry_attempt = attempt
                        telemetry_empty_reason = exc.code
                        telemetry_urn_present = exc.details.get("urn_present", False)
                        backoff = min(
                            empty_result_backoff_ms * (2 ** (attempt - 1)),
                            empty_result_max_backoff_ms,
                        )
                        jitter = random.randint(0, int(backoff * 0.3))
                        sleep_seconds = (backoff + jitter) / 1000.0
                        time.sleep(sleep_seconds)
                        continue
                    if (
                        api_mode == "cortensor_completion"
                        and self._api_mode_allows_completion_to_chat_fallback(
                            configured_api_mode
                        )
                    ):
                        response_payload = self._complete_via_openai_chat_mode(
                            request=request,
                            config=config,
                            model=model,
                            headers=headers,
                            tool_call_strategy=tool_call_strategy,
                            base_url=base_url,
                        )
                        parsed = self._parse_response_payload(
                            response_payload=response_payload,
                            request=request,
                            tool_call_strategy=tool_call_strategy,
                            fallback_model=model,
                            behavior_profile=behavior_profile,
                        )
                        break
                    raise
                raise

        if parsed is None and last_error is not None:
            trace_id = uuid.uuid4().hex[:8]
            enhanced_message = f"{last_error.message} (provider empty response after {attempt} attempts, trace_id={trace_id})"
            raise LLMCtlError(
                last_error.code,
                enhanced_message,
                details={
                    **last_error.details,
                    "trace_id": trace_id,
                    "attempts": attempt,
                },
            ) from last_error

        assert parsed is not None
        assert response_payload is not None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        assistant_messages = (
            [Message(role="assistant", content=parsed["text"])]
            if parsed["text"]
            else []
        )
        telemetry = {
            "attempt": telemetry_attempt or attempt,
            "empty_reason": telemetry_empty_reason or parsed.get("empty_reason"),
            "urn_present": telemetry_urn_present or parsed.get("urn_present", False),
            "urn_fetch_attempts": parsed.get("urn_fetch_attempts", 0),
            "final_outcome": "success",
        }
        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=parsed["model"],
                output_text=parsed["text"],
                assistant_messages=assistant_messages,
                tool_calls=parsed["tool_calls"],
                usage=parsed["usage"],
                latency_ms=elapsed_ms,
                finish_reason=str(parsed.get("finish_reason", "")).strip(),
                provider_raw=response_payload,
                telemetry=telemetry,
                normalization_meta={
                    "adapter": "cortensor",
                    "behavior_profile_id": behavior_profile.profile_id,
                    "api_mode": str(api_mode),
                    **parsed.get("tool_call_normalization", {}),
                },
            )
        )

    def _complete_via_openai_chat_mode(
        self,
        *,
        request: LLMRequest,
        config: dict[str, Any],
        model: str,
        headers: dict[str, str],
        tool_call_strategy: str,
        base_url: str,
    ) -> dict[str, Any]:
        payload = self._build_openai_chat_payload(
            request=request,
            config=config,
            model=model,
            tool_call_strategy=tool_call_strategy,
        )
        timeout_seconds = self._resolve_transport_timeout_seconds(
            api_mode="openai_chat",
            base_timeout_seconds=_as_int(config.get("timeout_seconds"), 420),
            completion_timeout_seconds=_as_int(
                payload.get("timeout"), _as_int(config.get("timeout_seconds"), 420)
            ),
            timeout_buffer_seconds=_as_int(
                config.get("transport_timeout_buffer_seconds"), 30
            ),
        )
        return self._post_with_retries(
            url=self._resolve_openai_chat_url(base_url),
            payload=payload,
            headers=headers,
            timeout_seconds=timeout_seconds,
            result_wait_attempts=self._resolve_positive_int(
                request.metadata.get("result_wait_attempts"),
                default_value=_as_int(config.get("result_wait_attempts"), 3),
            ),
            result_wait_interval_seconds=self._resolve_non_negative_float(
                request.metadata.get("result_wait_interval_seconds"),
                default_value=_as_float(
                    config.get("result_wait_interval_seconds"), 2.0
                ),
            ),
            trace_metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, Mapping) else None,
        )

    def _complete_via_completion_mode(
        self,
        *,
        request: LLMRequest,
        config: dict[str, Any],
        model: str,
        headers: dict[str, str],
        tool_call_strategy: str,
        base_url: str,
    ) -> dict[str, Any]:
        del tool_call_strategy
        candidates = self._resolve_session_candidates(request=request, config=config)
        if not candidates:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                "cortensor completion mode requires at least one valid session id",
            )

        result_wait_attempts = self._resolve_positive_int(
            request.metadata.get("result_wait_attempts"),
            default_value=_as_int(config.get("result_wait_attempts"), 3),
        )
        result_wait_interval_seconds = self._resolve_non_negative_float(
            request.metadata.get("result_wait_interval_seconds"),
            default_value=_as_float(config.get("result_wait_interval_seconds"), 2.0),
        )
        session_retry_rounds = self._resolve_positive_int(
            request.metadata.get("session_retry_rounds"),
            default_value=_as_int(config.get("session_retry_rounds"), 1),
        )
        failures: list[str] = []
        url = self._resolve_completion_url(base_url)
        for round_index in range(1, max(1, int(session_retry_rounds)) + 1):
            for session_id in candidates:
                payload = self._build_completion_payload(
                    request=request,
                    config=config,
                    model=model,
                    session_id=session_id,
                )
                timeout_seconds = self._resolve_transport_timeout_seconds(
                    api_mode="cortensor_completion",
                    base_timeout_seconds=_as_int(config.get("timeout_seconds"), 420),
                    completion_timeout_seconds=_as_int(
                        payload.get("timeout"),
                        _as_int(config.get("timeout_seconds"), 420),
                    ),
                    timeout_buffer_seconds=_as_int(
                        config.get("transport_timeout_buffer_seconds"), 30
                    ),
                )
                try:
                    return self._post_with_retries(
                        url=url,
                        payload=payload,
                        headers=headers,
                        timeout_seconds=timeout_seconds,
                        result_wait_attempts=result_wait_attempts,
                        result_wait_interval_seconds=result_wait_interval_seconds,
                        trace_metadata=request.metadata,
                        env=config.get("__env__")
                        if isinstance(config, Mapping)
                        else None,
                    )
                except LLMCtlError as exc:
                    failures.append(f"session_id={session_id}: {exc.message}")
                    if exc.code in {"AUTH_ERROR", "INVALID_ARGUMENT"}:
                        raise
            has_more_rounds = round_index < int(session_retry_rounds)
            if has_more_rounds and result_wait_interval_seconds > 0:
                time.sleep(result_wait_interval_seconds)
        raise LLMCtlError(
            "PROVIDER_ERROR",
            "Cortensor request failed: " + " | ".join(failures[-6:]),
        )

    def _post_with_retries(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: int,
        result_wait_attempts: int,
        result_wait_interval_seconds: float,
        trace_metadata: dict[str, Any] | None = None,
        env: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        max_attempts = max(1, int(result_wait_attempts))
        attempt = 1
        while attempt <= max_attempts:
            try:
                response_payload = _http_json_post(
                    url=url,
                    payload=payload,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    provider_name=self.name,
                    trace_metadata=trace_metadata,
                    env=env,
                )
            except LLMCtlError as exc:
                retryable = exc.code in {"TIMEOUT", "RATE_LIMITED"}
                if attempt < int(max_attempts) and retryable:
                    time.sleep(result_wait_interval_seconds)
                    attempt += 1
                    continue
                raise

            retry_reason = self._extract_retryable_reason(response_payload)
            if retry_reason == "offchain_result_pending":
                max_attempts = max(
                    max_attempts, int(self._OFFCHAIN_RESULT_MIN_WAIT_ATTEMPTS)
                )
            if retry_reason and attempt < int(max_attempts):
                time.sleep(result_wait_interval_seconds)
                attempt += 1
                continue

            error_message = self._extract_error_message(response_payload)
            if error_message:
                raise LLMCtlError("PROVIDER_ERROR", f"Cortensor error: {error_message}")

            if not self._response_has_text_or_tool_calls(response_payload):
                if self._looks_like_offchain_result_pending(response_payload):
                    max_attempts = max(
                        max_attempts, int(self._OFFCHAIN_RESULT_MIN_WAIT_ATTEMPTS)
                    )
                if attempt < int(max_attempts):
                    time.sleep(result_wait_interval_seconds)
                    attempt += 1
                    continue
            return response_payload

        raise LLMCtlError(
            "TIMEOUT", "Cortensor response remained pending after retry attempts"
        )

    def _response_has_text_or_tool_calls(self, payload: dict[str, Any]) -> bool:
        choices = payload.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            return False
        first_choice = choices[0]
        message_payload = first_choice.get("message")
        if isinstance(message_payload, dict):
            raw_tool_calls = message_payload.get("tool_calls")
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                return True
            text = _extract_message_text(message_payload.get("content"))
            if text:
                return True
        text_candidates = (
            first_choice.get("text"),
            first_choice.get("output_text"),
            payload.get("output_text"),
            payload.get("text"),
        )
        return any(bool(_extract_message_text(item)) for item in text_candidates)

    def _parse_response_payload(
        self,
        *,
        response_payload: dict[str, Any],
        request: LLMRequest,
        tool_call_strategy: str,
        fallback_model: str,
        behavior_profile: Any,
    ) -> dict[str, Any]:
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMCtlError("PROVIDER_ERROR", "Cortensor response missing choices")

        first_choice = choices[0] if isinstance(choices[0], dict) else None
        if not isinstance(first_choice, dict):
            raise LLMCtlError(
                "PROVIDER_ERROR", "Cortensor response has invalid choice payload"
            )

        message_payload = first_choice.get("message")
        allowed_tool_names = _resolve_tool_names(request)
        fallback_sources: list[ToolCallFallbackSource] = []
        if isinstance(message_payload, dict):
            fallback_sources.append(
                ToolCallFallbackSource(
                    source="message.content",
                    text=_extract_message_text(message_payload.get("content")),
                )
            )
        fallback_sources.extend(
            [
                ToolCallFallbackSource(
                    source="choice.text",
                    text=_extract_message_text(first_choice.get("text")),
                ),
                ToolCallFallbackSource(
                    source="choice.output_text",
                    text=_extract_message_text(first_choice.get("output_text")),
                ),
                ToolCallFallbackSource(
                    source="response.output_text",
                    text=_extract_message_text(response_payload.get("output_text")),
                ),
                ToolCallFallbackSource(
                    source="response.text",
                    text=_extract_message_text(response_payload.get("text")),
                ),
            ]
        )
        tool_call_resolution = resolve_tool_call_source_precedence(
            message_payload=message_payload,
            fallback_sources=fallback_sources,
            provider_name=self.name,
            model_name=str(response_payload.get("model") or fallback_model),
            allowed_tool_names=allowed_tool_names if request.tools else None,
            fallback_enabled=False,
            parser_plugin_selection=behavior_profile.parser_plugin_selection,
            fallback_parser_policy=behavior_profile.fallback_parser_policy,
        )
        tool_calls: list[ToolCall] = list(tool_call_resolution.calls)

        text = ""
        if isinstance(message_payload, dict):
            text = _extract_message_text(message_payload.get("content"))
        if not text:
            text = _extract_message_text(first_choice.get("text"))
        if not text:
            text = _extract_message_text(first_choice.get("output_text"))
        if not text:
            text = _extract_message_text(response_payload.get("output_text"))
        if not text:
            text = _extract_message_text(response_payload.get("text"))

        tool_calls = _coerce_tool_calls(tool_calls)

        if not text and not tool_calls:
            response_str = str(response_payload)
            has_urn = "urn:" in response_str.lower() or "task_id" in response_str
            if has_urn:
                raise LLMCtlError(
                    "EMPTY_URN_CONTENT",
                    "Cortensor response contains URN but no resolvable content (off-chain result pending)",
                    details={"retryable": True, "urn_present": True},
                )
            has_text_field = (
                (isinstance(message_payload, dict) and "content" in message_payload)
                or (isinstance(first_choice, dict) and "text" in first_choice)
                or (isinstance(first_choice, dict) and "output_text" in first_choice)
                or ("text" in response_payload)
                or ("output_text" in response_payload)
            )
            if not has_text_field:
                raise LLMCtlError(
                    "MALFORMED_PAYLOAD",
                    "Cortensor response has malformed or missing payload structure",
                    details={"retryable": False},
                )
            raise LLMCtlError(
                "EMPTY_PAYLOAD",
                "Cortensor response did not include text or tool calls",
                details={"retryable": True},
            )

        return {
            "text": text.strip(),
            "tool_calls": tool_calls,
            "tool_call_normalization": tool_call_resolution.as_metadata(),
            "usage": _usage_from_openai_like(response_payload.get("usage")),
            "model": str(response_payload.get("model") or fallback_model),
            "finish_reason": str(first_choice.get("finish_reason", "")).strip(),
        }

    def _build_openai_chat_payload(
        self,
        *,
        request: LLMRequest,
        config: dict[str, Any],
        model: str,
        tool_call_strategy: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_openai_like(
                request,
                include_fallback_instruction=bool(
                    request.tools and supports_fallback_tool_calling(tool_call_strategy)
                ),
            ),
            "temperature": _as_float(
                request.temperature
                if request.temperature is not None
                else config.get("temperature"),
                0.2,
            ),
        }
        top_p = request.top_p if request.top_p is not None else config.get("top_p")
        if top_p is not None:
            payload["top_p"] = _as_float(top_p, 1.0)
        max_tokens = self._resolve_max_tokens(request=request, config=config)
        payload["max_tokens"] = int(max_tokens)

        if request.tools and supports_native_tool_calling(tool_call_strategy):
            payload["tools"] = build_openai_tools_payload(request.tools)
            payload["tool_choice"] = normalize_tool_choice(request.tool_choice)
        return payload

    def _build_completion_payload(
        self,
        *,
        request: LLMRequest,
        config: dict[str, Any],
        model: str,
        session_id: int,
    ) -> dict[str, Any]:
        timeout_seconds = self._resolve_positive_int(
            request.metadata.get("timeout_seconds"),
            default_value=_as_int(config.get("timeout_seconds"), 420),
        )
        precommit_timeout = self._resolve_positive_int(
            request.metadata.get("precommit_timeout_seconds"),
            default_value=_as_int(config.get("precommit_timeout_seconds"), 300),
        )
        timeout_buffer_seconds = max(
            0, _as_int(config.get("transport_timeout_buffer_seconds"), 30)
        )
        timeout_headroom_seconds = max(
            timeout_buffer_seconds, int(self._MIN_TIMEOUT_HEADROOM_SECONDS)
        )
        payload_timeout_floor = int(precommit_timeout) + timeout_headroom_seconds
        payload_timeout_seconds = max(int(timeout_seconds), payload_timeout_floor)

        return {
            "model": model,
            "session_id": int(session_id),
            "prompt": self._build_completion_prompt(request),
            "max_tokens": int(self._resolve_max_tokens(request=request, config=config)),
            "temperature": _as_float(
                request.temperature
                if request.temperature is not None
                else config.get("temperature"),
                0.2,
            ),
            "top_p": _as_float(
                request.top_p if request.top_p is not None else config.get("top_p"), 1.0
            ),
            "top_k": int(_as_int(config.get("top_k"), 0)),
            "presence_penalty": _as_float(config.get("presence_penalty"), 0.0),
            "frequency_penalty": _as_float(config.get("frequency_penalty"), 0.0),
            "node_type": int(_as_int(config.get("node_type"), 0)),
            "prompt_type": 1,
            "stream": bool(config.get("stream", False)),
            "timeout": int(payload_timeout_seconds),
            "precommit_timeout": int(precommit_timeout),
            "privacy_level": str(config.get("privacy_level", "high") or "high"),
            "client_reference": str(request.metadata.get("client_reference", "")),
        }

    def _build_completion_prompt(self, request: LLMRequest) -> str:
        sections: list[str] = []
        messages: list[tuple[str, str]] = []
        for msg in request.messages:
            content = str(msg.content or "").strip()
            if not content:
                continue
            role = (
                msg.role
                if msg.role in {"system", "user", "assistant", "tool"}
                else "user"
            )
            if role == "tool":
                role = "assistant"
            messages.append((role, content))

        if not messages:
            return "user:\n\nassistant:"

        current_user = ""
        history_messages = messages
        if messages and messages[-1][0] == "user":
            current_user = messages[-1][1]
            history_messages = messages[:-1]
        else:
            current_user = messages[-1][1]
            history_messages = messages[:-1]

        system_chunks = [
            content
            for role, content in history_messages
            if role == "system" and content.strip()
        ]
        if system_chunks:
            sections.append("System instruction:\n" + "\n\n".join(system_chunks))

        if request.tools and supports_fallback_tool_calling(
            request.metadata.get("tool_call_strategy", "hybrid")
        ):
            fallback_instruction = build_fallback_tool_call_instruction(request.tools)
            if fallback_instruction:
                sections.append(fallback_instruction)

        filtered_history: list[tuple[str, str]] = [
            (role, content)
            for role, content in history_messages
            if role in {"user", "assistant"}
        ]
        while (
            filtered_history
            and filtered_history[-1][0] == "user"
            and filtered_history[-1][1].strip() == current_user.strip()
        ):
            filtered_history.pop()

        history_lines: list[str] = []
        for role, content in filtered_history:
            history_lines.append(f"{role}: {content}")
        if history_lines:
            sections.append("Conversation history:\n" + "\n".join(history_lines))

        sections.append(f"user: {current_user.strip()}")
        sections.append("assistant:")
        return "\n\n".join(section for section in sections if section.strip())

    def _resolve_max_tokens(
        self, *, request: LLMRequest, config: dict[str, Any]
    ) -> int:
        configured_max_tokens = self._resolve_positive_int(
            config.get("max_tokens"), default_value=4096
        )
        request_max_tokens = self._resolve_positive_int(
            request.max_output_tokens, default_value=0
        )
        metadata_max_tokens = self._resolve_positive_int(
            request.metadata.get("max_tokens"), default_value=0
        )
        explicit_max_tokens = max(request_max_tokens, metadata_max_tokens)
        resolved = (
            explicit_max_tokens if explicit_max_tokens > 0 else configured_max_tokens
        )

        min_tokens = int(self._DEFAULT_MAX_TOKENS_FLOOR)
        input_estimate = self._resolve_positive_int(
            request.metadata.get("input_tokens_estimate"), default_value=0
        )
        if input_estimate >= 12000:
            min_tokens = max(min_tokens, 8192)
        elif input_estimate >= 8000:
            min_tokens = max(min_tokens, 6144)

        return max(int(resolved), int(min_tokens))

    def _resolve_session_candidates(
        self, *, request: LLMRequest, config: dict[str, Any]
    ) -> list[int]:
        results: list[int] = []
        seen: set[int] = set()

        def _append(values: list[int]) -> None:
            for item in values:
                parsed = int(item)
                if parsed <= 0 or parsed in seen:
                    continue
                seen.add(parsed)
                results.append(parsed)

        metadata_session_id = self._resolve_positive_int(
            request.metadata.get("session_id"), default_value=0
        )
        if metadata_session_id > 0:
            _append([metadata_session_id])

        _append(self._parse_positive_int_list(request.metadata.get("session_ids")))

        metadata_pool = str(request.metadata.get("session_pool", "")).strip().lower()
        configured_pool = (
            str(config.get("session_pool", "auto")).strip().lower() or "auto"
        )
        session_pool = metadata_pool or configured_pool

        metadata_dedicated = self._parse_positive_int_list(
            request.metadata.get("dedicated_session_ids")
        )
        metadata_ephemeral = self._parse_positive_int_list(
            request.metadata.get("ephemeral_session_ids")
        )
        dedicated = metadata_dedicated or self._parse_positive_int_list(
            config.get("dedicated_session_ids")
        )
        ephemeral = metadata_ephemeral or self._parse_positive_int_list(
            config.get("ephemeral_session_ids")
        )

        legacy = [
            self._resolve_positive_int(config.get("session_id"), default_value=0),
            *self._parse_positive_int_list(config.get("session_ids")),
        ]

        if session_pool == "ephemeral":
            ordered = [ephemeral, legacy, dedicated]
        elif session_pool in {"dedicated", "auto"}:
            ordered = [dedicated, legacy, ephemeral]
        else:
            ordered = [legacy, dedicated, ephemeral]

        for group in ordered:
            _append(group)
        return results

    def _extract_error_message(self, payload: dict[str, Any]) -> str:
        error_payload = payload.get("error")
        if isinstance(error_payload, str):
            return error_payload.strip()
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            code = error_payload.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
        message_payload = payload.get("message")
        if isinstance(message_payload, str) and message_payload.strip():
            return message_payload.strip()
        return ""

    def _extract_retryable_reason(self, payload: dict[str, Any]) -> str:
        status = str(payload.get("status", "")).strip().lower()
        if status in {"queued", "pending", "processing", "running", "in_progress"}:
            return status
        if self._looks_like_offchain_result_pending(payload):
            return "offchain_result_pending"
        message = (self._extract_error_message(payload) or "").lower()
        retry_tokens = (
            "timeout",
            "timed out",
            "pending",
            "queued",
            "not ready",
            "in progress",
            "still processing",
            "temporarily unavailable",
            "try again",
            "rate limit",
        )
        if any(token in message for token in retry_tokens):
            return message
        return ""

    def _looks_like_offchain_result_pending(self, payload: dict[str, Any]) -> bool:
        try:
            serialized = json.dumps(payload, sort_keys=True, default=str).lower()
        except Exception:
            serialized = str(payload).lower()
        if not serialized:
            return False

        has_urn = (
            "urn:blob" in serialized
            or " off-chain result " in serialized
            or "offchain result" in serialized
        )
        if not has_urn:
            return False

        if not self._response_has_text_or_tool_calls(payload):
            return True

        pending_tokens = (
            "no content",
            "not found",
            "does not exist",
            "missing object",
            "s3",
            "off-chain result unavailable",
            "offchain result unavailable",
            "result unavailable",
        )
        return any(token in serialized for token in pending_tokens)

    def _resolve_transport_timeout_seconds(
        self,
        *,
        api_mode: str,
        base_timeout_seconds: int,
        completion_timeout_seconds: int,
        timeout_buffer_seconds: int,
    ) -> int:
        timeout_seconds = int(base_timeout_seconds)
        if api_mode == "cortensor_completion":
            buffer_seconds = max(0, int(timeout_buffer_seconds))
            timeout_seconds = max(
                timeout_seconds, int(completion_timeout_seconds) + buffer_seconds
            )
        return max(1, int(timeout_seconds))

    def _resolve_api_mode(self, *, configured_mode: str, base_url: str) -> str:
        normalized = (configured_mode or "").strip().lower()
        if normalized in {"openai_chat", "cortensor_completion"}:
            return normalized
        path = urlparse(base_url).path.strip().lower()
        if path.endswith("/completions"):
            return "cortensor_completion"
        return "openai_chat"

    def _api_mode_allows_completion_to_chat_fallback(
        self, configured_mode: str
    ) -> bool:
        normalized = (configured_mode or "").strip().lower()
        return normalized in {"", "auto"}

    def _resolve_openai_chat_url(self, base_url: str) -> str:
        normalized = self._normalize_cortensor_base_url(base_url).rstrip("/")
        if normalized.lower().endswith("/completions"):
            normalized = normalized[: -len("/completions")]
        if normalized.lower().endswith("/chat/completions"):
            return normalized
        return f"{normalized}/chat/completions"

    def _resolve_completion_url(self, base_url: str) -> str:
        normalized = self._normalize_cortensor_base_url(base_url).rstrip("/")
        if normalized.lower().endswith("/completions"):
            return normalized
        return f"{normalized}/completions"

    def _normalize_cortensor_base_url(self, base_url: str) -> str:
        normalized = str(base_url or "").strip()
        if not normalized:
            return self._DEFAULT_BASE_URL

        parsed = urlparse(normalized)
        path = parsed.path or ""
        lowered_path = path.lower()
        replacement_path: str | None = None
        marker = "/api/v1/"
        marker_length = len(marker)

        marker_index = lowered_path.find(marker)
        if marker_index >= 0:
            replacement_path = (
                f"{path[:marker_index]}/api/v2/{path[marker_index + marker_length :]}"
            )
        elif lowered_path.endswith("/api/v1"):
            replacement_path = f"{path[: -len('/api/v1')]}/api/v2"

        if replacement_path is None:
            return normalized

        return urlunparse(parsed._replace(path=replacement_path))

    def _resolve_positive_int(self, raw_value: Any, *, default_value: int) -> int:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            return int(default_value)
        if parsed <= 0:
            return int(default_value)
        return parsed

    def _resolve_non_negative_float(
        self, raw_value: Any, *, default_value: float
    ) -> float:
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            return float(default_value)
        if parsed < 0:
            return float(default_value)
        return float(parsed)

    def _parse_positive_int_list(self, raw_value: Any) -> list[int]:
        parsed_values: list[int] = []
        source_values: list[Any]
        if isinstance(raw_value, list):
            source_values = raw_value
        elif isinstance(raw_value, str):
            source_values = [part.strip() for part in raw_value.split(",")]
        else:
            return parsed_values
        for item in source_values:
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                parsed_values.append(parsed)
        return parsed_values

    def list_models(self, config: dict[str, Any]) -> list[str]:
        return _list_models_from_config(config)

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


def cortensor_provider() -> CortensorProvider:
    return CortensorProvider()
