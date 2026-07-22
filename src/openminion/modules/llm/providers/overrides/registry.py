from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from collections.abc import Mapping, Sequence

from openminion.base.config.env import resolve_environment_config

if TYPE_CHECKING:
    from openminion.modules.llm.providers.behavior.contracts import (
        RetryOverridePolicy,
    )

_DISABLE_PROVIDER_OVERRIDES_ENV = "OPENMINION_DISABLE_PROVIDER_OVERRIDES"
_DISABLE_PROVIDER_OVERRIDES_METADATA_KEYS = (
    "provider_override_mode",
    "disable_provider_overrides",
)


@dataclass(frozen=True)
class ProviderRetryOverride:
    override_id: str
    provider_names: tuple[str, ...]
    model_fragments: tuple[str, ...]
    phase_purposes: tuple[str, ...]
    requires_thinking: bool = False
    schema_only_submit_output: bool = False
    retry_tool_choice: str | dict[str, Any] = "auto"
    notes: str = ""
    rollback_hint: str = (
        "Set OPENMINION_DISABLE_PROVIDER_OVERRIDES=1 or request metadata "
        "provider_override_mode=disabled to turn off provider-specific retries."
    )


@dataclass(frozen=True)
class ProviderRetryOverrideResolution:
    matched: bool
    override_id: str = ""
    retry_tool_choice: str | dict[str, Any] | None = None
    notes: str = ""
    rollback_hint: str = ""
    disabled: bool = False
    disabled_reason: str = ""


_PROVIDER_RETRY_OVERRIDES: tuple[ProviderRetryOverride, ...] = (
    ProviderRetryOverride(
        override_id="openai_structured_thinking_tool_choice_retry",
        provider_names=("openai",),
        model_fragments=(),
        phase_purposes=("decide", "plan", "judge", "validate"),
        requires_thinking=True,
        schema_only_submit_output=True,
        retry_tool_choice="auto",
        notes=(
            "Some OpenAI-compatible thinking-mode providers reject "
            "function-targeted or required tool_choice for submit_output-only "
            "structured phases. Retry once with tool_choice=auto while "
            "preserving the same submit_output-only tool catalog."
        ),
    ),
    ProviderRetryOverride(
        override_id="openrouter_glm_minimax_tool_choice_required_retry",
        provider_names=("openrouter",),
        model_fragments=("glm", "minimax"),
        phase_purposes=(),
        requires_thinking=False,
        schema_only_submit_output=False,
        retry_tool_choice="auto",
        notes=(
            "GLM-5-Turbo and MiniMax M2.7 via OpenRouter reject tool_choice=required "
            "and function-targeted tool_choice values with HTTP 400/404. "
            "Retry once with tool_choice=auto to fall back to model-chosen tool use. "
            "Applies to all request phases since the rejection is at the provider "
            "request-contract layer, not specific to structured-output phases."
        ),
    ),
)


def filter_provider_retry_overrides(
    provider_name: str,
) -> tuple[ProviderRetryOverride, ...]:
    """Return overrides whose `provider_names` admit `provider_name`."""

    normalized = str(provider_name or "").strip().lower()
    return tuple(
        item
        for item in _PROVIDER_RETRY_OVERRIDES
        if not item.provider_names or normalized in item.provider_names
    )


def provider_retry_overrides_disabled(
    *,
    metadata: Mapping[str, Any] | None = None,
    env: Mapping[str, object] | None = None,
) -> tuple[bool, str]:
    """Return `(disabled, reason)` for provider retry-override hooks."""

    if _provider_overrides_disabled(metadata=metadata, env=env):
        return True, "provider overrides explicitly disabled"
    return False, ""


def provider_retry_override_table() -> list[dict[str, Any]]:
    return [
        {
            "override_id": item.override_id,
            "provider_names": list(item.provider_names),
            "model_fragments": list(item.model_fragments),
            "phase_purposes": list(item.phase_purposes),
            "requires_thinking": item.requires_thinking,
            "schema_only_submit_output": item.schema_only_submit_output,
            "retry_tool_choice": item.retry_tool_choice,
            "notes": item.notes,
            "rollback_hint": item.rollback_hint,
        }
        for item in _PROVIDER_RETRY_OVERRIDES
    ]


def resolve_provider_retry_override(
    *,
    provider_name: str,
    model_name: str,
    purpose: str,
    thinking: str | None,
    tool_choice: str | dict[str, Any] | None,
    tool_names: Sequence[str] | None,
    metadata: Mapping[str, Any] | None = None,
    env: Mapping[str, object] | None = None,
    policy: "RetryOverridePolicy | None" = None,
) -> ProviderRetryOverrideResolution:
    """Resolve the retry override for one provider call."""

    if policy is not None:
        if policy.disabled:
            return ProviderRetryOverrideResolution(
                matched=False,
                disabled=True,
                disabled_reason=policy.disabled_reason
                or "provider overrides explicitly disabled",
            )
        candidates = policy.applicable_overrides
    else:
        if _provider_overrides_disabled(metadata=metadata, env=env):
            return ProviderRetryOverrideResolution(
                matched=False,
                disabled=True,
                disabled_reason="provider overrides explicitly disabled",
            )
        candidates = _PROVIDER_RETRY_OVERRIDES

    normalized_provider = str(provider_name or "").strip().lower()
    normalized_model = str(model_name or "").strip().lower()
    normalized_purpose = str(purpose or "").strip().lower()
    has_thinking = bool(str(thinking or "").strip())
    submit_output_only = _submit_output_only_tools(tool_names)

    for item in candidates:
        if item.provider_names and normalized_provider not in item.provider_names:
            continue
        if item.model_fragments and not any(
            fragment in normalized_model for fragment in item.model_fragments
        ):
            continue
        if item.phase_purposes and normalized_purpose not in item.phase_purposes:
            continue
        if item.requires_thinking and not has_thinking:
            continue
        if item.schema_only_submit_output and not submit_output_only:
            continue
        if not _structured_tool_choice(tool_choice):
            continue
        return ProviderRetryOverrideResolution(
            matched=True,
            override_id=item.override_id,
            retry_tool_choice=item.retry_tool_choice,
            notes=item.notes,
            rollback_hint=item.rollback_hint,
        )

    return ProviderRetryOverrideResolution(matched=False)


def _provider_overrides_disabled(
    *,
    metadata: Mapping[str, Any] | None,
    env: Mapping[str, object] | None,
) -> bool:
    env_config = resolve_environment_config(env=env)
    if env_config.get_bool(_DISABLE_PROVIDER_OVERRIDES_ENV, False):
        return True

    raw_metadata = dict(metadata or {})
    mode = str(raw_metadata.get("provider_override_mode", "") or "").strip().lower()
    if mode in {"disable", "disabled", "off", "none"}:
        return True
    for key in _DISABLE_PROVIDER_OVERRIDES_METADATA_KEYS:
        if _truthy(raw_metadata.get(key)):
            return True
    return False


def _structured_tool_choice(value: str | dict[str, Any] | None) -> bool:
    if isinstance(value, dict):
        if str(value.get("type", "")).strip().lower() != "function":
            return False
        payload = value.get("function")
        if not isinstance(payload, Mapping):
            return False
        return str(payload.get("name", "")).strip() == "submit_output"
    if isinstance(value, str):
        return value.strip().lower() == "required"
    return False


def _submit_output_only_tools(tool_names: Sequence[str] | None) -> bool:
    normalized = tuple(
        str(item or "").strip()
        for item in (tool_names or [])
        if str(item or "").strip()
    )
    return bool(normalized) and all(item == "submit_output" for item in normalized)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
