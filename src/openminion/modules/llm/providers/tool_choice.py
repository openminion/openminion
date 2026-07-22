"""Provider tool-choice compatibility and retry helpers."""

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from openminion.base.config.env import EnvironmentConfig

if TYPE_CHECKING:
    from openminion.modules.llm.providers.behavior.contracts import (
        RetryOverridePolicy,
    )


@dataclass(frozen=True)
class ProviderRetryCompletionResult:
    response: Any
    normalized_tool_choice: str | dict[str, Any]
    retry_override_id: str = ""


def normalize_provider_tool_choice(value: Any) -> str | dict[str, Any]:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"auto", "none", "required"}:
            return normalized
        return "auto"
    if isinstance(value, dict):
        return dict(value)
    return "auto"


def should_retry_with_auto_tool_choice(error: Any, tool_choice: Any) -> bool:
    if tool_choice is None:
        return False
    if isinstance(tool_choice, str) and tool_choice in {"auto", "none"}:
        return False
    code = str(getattr(error, "code", "") or "").strip().upper()
    message = str(getattr(error, "message", "") or "").strip().lower()
    if code not in {"PROVIDER_ERROR", "INVALID_ARGUMENT", "BAD_REQUEST"}:
        return False
    # Match both API-style ("tool_choice") and natural-language-style ("tool choice")
    if (
        "tool_choice" not in message
        and "tool choice" not in message
        and "chat setting" not in message
    ):
        return False
    return True


def complete_with_provider_override_retry(
    *,
    complete_fn: Callable[..., Any],
    provider_name: str,
    model_name: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    tool_choice: Any,
    metadata: Mapping[str, Any] | None,
    thinking: str | None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    policy: "RetryOverridePolicy | None" = None,
) -> ProviderRetryCompletionResult:
    from openminion.modules.llm.providers.overrides import (
        resolve_provider_retry_override,
    )

    metadata_payload = {
        str(key): str(value) for key, value in dict(metadata or {}).items()
    }
    normalized_tool_choice = normalize_provider_tool_choice(tool_choice)
    tool_payload = [dict(tool) for tool in tools or []]
    retry_override = resolve_provider_retry_override(
        provider_name=provider_name,
        model_name=model_name,
        purpose=str(metadata_payload.get("purpose", "") or "").strip().lower(),
        thinking=thinking,
        tool_choice=normalized_tool_choice,
        tool_names=[
            str(tool.get("name", "") or "").strip()
            for tool in tool_payload
            if str(tool.get("name", "") or "").strip()
        ],
        metadata=metadata,
        env=env,
        policy=policy,
    )

    request_kwargs = {
        "messages": list(messages),
        "tools": tool_payload or None,
        "provider": provider_name,
        "model": model_name,
        "tool_choice": normalized_tool_choice,
        "metadata": metadata_payload,
    }
    if not tool_payload and normalized_tool_choice == "required":
        request_kwargs["tool_choice"] = "auto"
    response = complete_fn(**request_kwargs)
    retry_override_id = ""
    if (
        not bool(getattr(response, "ok", False))
        and retry_override.matched
        and should_retry_with_auto_tool_choice(
            getattr(response, "error", None),
            normalized_tool_choice,
        )
    ):
        retry_override_id = retry_override.override_id
        request_kwargs["tool_choice"] = retry_override.retry_tool_choice
        response = complete_fn(**request_kwargs)

    return ProviderRetryCompletionResult(
        response=response,
        normalized_tool_choice=normalized_tool_choice,
        retry_override_id=retry_override_id,
    )
