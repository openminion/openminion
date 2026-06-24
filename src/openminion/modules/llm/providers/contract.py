"""Provider protocol and built-in provider implementations."""

import json
import logging
import re
from typing import Any, Iterator, Protocol

from openminion.base.config.env import resolve_environment_config

from ..constants import (
    LLM_TOOL_CALL_STATUS_ERROR,
    LLM_TOOL_CALL_STATUS_PARSED,
    LLM_TOOL_CALL_STATUS_REQUESTED,
)
from ..contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ..errors import LLMCtlError
from ..interfaces import LLM_RESPONSE_INTERFACE_VERSION, PROVIDER_INTERFACE_VERSION
from ..schemas import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    Message,
    ResponseError,
    ToolCall,
    UsageInfo,
)

__all__ = ["PROVIDER_INTERFACE_VERSION", "Provider", "ensure_provider"]

_LOG = logging.getLogger(__name__)


class Provider(Protocol):
    name: str
    contract_version: str
    provider_interface_version: str

    def complete(
        self, request: LLMRequest, config: dict[str, Any]
    ) -> LLMResponse | ProviderAdapterResult: ...

    def stream(
        self, request: LLMRequest, config: dict[str, Any]
    ) -> Iterator[LLMStreamEvent]: ...

    def list_models(self, config: dict[str, Any]) -> list[str]: ...

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]: ...


def ensure_provider(provider: Any, *, component_name: str | None = None) -> None:
    """Validate that a candidate object satisfies the Layer-2 Provider contract."""

    name = str(getattr(provider, "name", "") or "").strip()
    if not name:
        label = component_name or "provider"
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            f"{label} must expose non-empty 'name'",
        )

    contract_version = str(getattr(provider, "contract_version", "") or "").strip()
    if not contract_version:
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            f"Provider '{name}' missing 'contract_version'",
            {"provider": name},
        )

    required_methods = ("complete", "list_models", "healthcheck")
    for method in required_methods:
        if not callable(getattr(provider, method, None)):
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                f"Provider '{name}' missing '{method}' method",
                {"provider": name, "method": method},
            )

    raw_interface_version = getattr(provider, "provider_interface_version", None)
    interface_version = str(raw_interface_version or "").strip()
    strict = resolve_environment_config().openminion_provider_interface_strict
    if interface_version != PROVIDER_INTERFACE_VERSION:
        message = (
            f"Provider '{name}' provider_interface_version mismatch: "
            f"expected={PROVIDER_INTERFACE_VERSION!r} actual={raw_interface_version!r}"
        )
        if strict:
            raise LLMCtlError(
                "PROVIDER_CONTRACT_VIOLATION",
                message,
                details={
                    "provider": name,
                    "expected_provider_interface_version": PROVIDER_INTERFACE_VERSION,
                    "actual_provider_interface_version": raw_interface_version,
                },
            )
        _LOG.warning(message)


def _estimate_usage(messages: list[Message], output_text: str) -> UsageInfo:
    in_chars = sum(len(msg.content) for msg in messages)
    out_chars = len(output_text)
    input_tokens = max(1, in_chars // 4) if in_chars else 0
    output_tokens = max(1, out_chars // 4) if out_chars else 0
    total = input_tokens + output_tokens
    return UsageInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
    )


def _last_user_text(messages: list[Message]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return ""


class StubProvider:
    name = "stub"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        del config
        model_name = request.model or "stub-v1"
        user_text = _last_user_text(request.messages)

        forced_error = request.metadata.get("force_error_code")
        if isinstance(forced_error, str):
            return adapter_result_to_llm_response(
                ProviderAdapterResult(
                    provider=self.name,
                    model=model_name,
                    output_text="",
                    assistant_messages=[],
                    tool_calls=[],
                    usage=_estimate_usage(request.messages, ""),
                    latency_ms=0,
                    provider_raw={"forced_error": forced_error},
                    finish_reason="error",
                    normalization_meta={"adapter": "stub", "path": "forced_error"},
                    error=ResponseError(
                        code=forced_error,  # pydantic validates against allowed literals
                        message="Forced stub error",
                        details={"provider": self.name},
                    ),
                )
            )

        tool_calls: list[ToolCall] = []
        output_text = f"stub:{user_text}" if user_text else "stub:ok"

        # Directive format for testing post-call tool policy:
        # tool:<name> {"key":"value"}
        match = re.match(r"^tool:([\w\.-]+)\s+(.+)$", user_text.strip())
        if match:
            tool_name = match.group(1)
            raw_args = match.group(2)
            parsed_args: dict[str, Any] = {}
            status = LLM_TOOL_CALL_STATUS_REQUESTED
            error: str | None = None
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    parsed_args = parsed
                    status = LLM_TOOL_CALL_STATUS_PARSED
                else:
                    error = "Tool call args must be a JSON object"
                    status = LLM_TOOL_CALL_STATUS_ERROR
            except json.JSONDecodeError as exc:
                error = f"Invalid tool args JSON: {exc}"
                status = LLM_TOOL_CALL_STATUS_ERROR

            tool_calls.append(
                ToolCall(
                    name=tool_name,
                    arguments=parsed_args,
                    raw_arguments=raw_args,
                    status=status,
                    error=error,
                )
            )
            output_text = ""

        assistant_messages = (
            [Message(role="assistant", content=output_text)] if output_text else []
        )

        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=model_name,
                output_text=output_text,
                assistant_messages=assistant_messages,
                tool_calls=tool_calls,
                usage=_estimate_usage(request.messages, output_text),
                latency_ms=0,
                cost_usd=0.0,
                finish_reason="tool_calls" if tool_calls else "stop",
                provider_raw={"provider": self.name, "echo": user_text},
                normalization_meta={"adapter": "stub", "path": "default"},
            )
        )

    def stream(
        self, request: LLMRequest, config: dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        response = self.complete(request, config)
        if response.error is not None:
            yield LLMStreamEvent(type="error", error=response.error)
            return
        if response.output_text:
            yield LLMStreamEvent(type="delta", delta_text=response.output_text)
        yield LLMStreamEvent(type="done")

    def list_models(self, config: dict[str, Any]) -> list[str]:
        del config
        return ["stub-v1", "stub-v2"]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


class LocalProvider:
    name = "local"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        del config
        model_name = request.model or "local-echo"
        user_text = _last_user_text(request.messages)
        output_text = user_text if user_text else ""
        assistant_messages = (
            [Message(role="assistant", content=output_text)] if output_text else []
        )
        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=model_name,
                output_text=output_text,
                assistant_messages=assistant_messages,
                tool_calls=[],
                usage=_estimate_usage(request.messages, output_text),
                latency_ms=0,
                cost_usd=0.0,
                finish_reason="stop",
                provider_raw={"provider": self.name},
                normalization_meta={"adapter": "local", "path": "echo"},
            )
        )

    def stream(
        self, request: LLMRequest, config: dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        response = self.complete(request, config)
        if response.output_text:
            yield LLMStreamEvent(type="delta", delta_text=response.output_text)
        yield LLMStreamEvent(type="done")

    def list_models(self, config: dict[str, Any]) -> list[str]:
        del config
        return ["local-echo"]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


def stub_provider() -> StubProvider:
    return StubProvider()


def local_provider() -> LocalProvider:
    return LocalProvider()
