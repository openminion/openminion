import json
import re
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Sequence

from openminion.modules.llm.providers.base import (
    PROVIDER_RESPONSE_INTERFACE_VERSION,
    ProviderError,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.llm.constants import DEFAULT_FINISH_REASON_ALIASES
from openminion.modules.llm.providers.tool_calling import (
    detect_raw_envelope,
    detect_raw_xml_tool_wrapper,
    sanitize_envelope_leak,
)

_EMPTY_PROVIDER_RESPONSE_TEXT = (
    "I could not parse a usable model response on this turn. Please retry."
)


@dataclass(frozen=True)
class ProviderResponseNormalizationProfile:
    name: str
    provider_name: str = ""
    model_pattern: re.Pattern[str] | None = None
    recover_empty_payload: bool = True
    fallback_text: str = _EMPTY_PROVIDER_RESPONSE_TEXT
    finish_reason_aliases: dict[str, str] = field(default_factory=dict)

    def matches(self, *, provider_name: str, model_name: str) -> bool:
        provider_match = not self.provider_name or self.provider_name == provider_name
        model_match = not self.model_pattern or bool(
            self.model_pattern.search(model_name)
        )
        return provider_match and model_match


_NORMALIZATION_PROFILES: list[ProviderResponseNormalizationProfile] = [
    ProviderResponseNormalizationProfile(
        name="openrouter-oss",
        provider_name="openrouter",
        model_pattern=re.compile(r"(?i)\boss\b|openrouter/.+oss|oss[-_/]?\d"),
        recover_empty_payload=True,
        fallback_text="I received an empty response from this OpenRouter model. Please retry.",
        finish_reason_aliases={
            **DEFAULT_FINISH_REASON_ALIASES,
            "max_output_tokens": "length",
        },
    ),
    ProviderResponseNormalizationProfile(
        name="openrouter-default",
        provider_name="openrouter",
        recover_empty_payload=True,
        fallback_text="I received an empty response from OpenRouter. Please retry.",
        finish_reason_aliases=dict(DEFAULT_FINISH_REASON_ALIASES),
    ),
    ProviderResponseNormalizationProfile(
        name="anthropic-default",
        provider_name="anthropic",
        recover_empty_payload=True,
        fallback_text="I received an empty response from Anthropic. Please retry.",
        finish_reason_aliases=dict(DEFAULT_FINISH_REASON_ALIASES),
    ),
    ProviderResponseNormalizationProfile(
        name="default",
        recover_empty_payload=True,
        fallback_text=_EMPTY_PROVIDER_RESPONSE_TEXT,
        finish_reason_aliases=dict(DEFAULT_FINISH_REASON_ALIASES),
    ),
]


def is_provider_recovery_fallback_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(
        normalized == str(profile.fallback_text or "").strip().lower()
        for profile in _NORMALIZATION_PROFILES
    )


def resolve_normalization_profile(
    *,
    provider_name: str = "",
    model_name: str = "",
) -> ProviderResponseNormalizationProfile:
    provider = str(provider_name or "").strip().lower()
    model = str(model_name or "").strip().lower()
    for profile in _NORMALIZATION_PROFILES:
        if profile.matches(provider_name=provider, model_name=model):
            return profile
    return _NORMALIZATION_PROFILES[-1]


def normalize_provider_response(
    response: ProviderResponse | Any,
    *,
    provider_name: str = "",
    allowed_tool_names: Sequence[str] | None = None,
    model_name: str = "",
    profile: ProviderResponseNormalizationProfile | None = None,
    recover_empty_payload: bool | None = None,
    fallback_text: str | None = None,
) -> ProviderResponse:
    """Normalize provider output to a canonical ProviderResponse shape.

    This function is intentionally provider-agnostic and safe to call multiple times.
    """
    canonical_response = _coerce_provider_response(response)
    if profile is None:
        profile = resolve_normalization_profile(
            provider_name=str(provider_name or canonical_response.model or ""),
            model_name=str(model_name or canonical_response.model or ""),
        )
    effective_recover_empty_payload = (
        profile.recover_empty_payload
        if recover_empty_payload is None
        else bool(recover_empty_payload)
    )
    effective_fallback_text = (
        str(profile.fallback_text or _EMPTY_PROVIDER_RESPONSE_TEXT)
        if fallback_text is None
        else str(fallback_text or _EMPTY_PROVIDER_RESPONSE_TEXT)
    )

    text = str(canonical_response.text or "").strip()
    model = (
        str(canonical_response.model or "").strip()
        or str(provider_name or "").strip()
        or "unknown-model"
    )
    usage = _normalize_usage(canonical_response.usage)
    finish_reason = _canonical_finish_reason(
        str(canonical_response.finish_reason or "").strip(),
        aliases=profile.finish_reason_aliases or DEFAULT_FINISH_REASON_ALIASES,
    )
    normalization = (
        dict(canonical_response.normalization)
        if isinstance(canonical_response.normalization, dict)
        else {}
    )

    tool_calls = _normalize_tool_calls(canonical_response.tool_calls)
    if _thinking_passthrough_enabled():
        thinking = _coerce_thinking_blocks(
            getattr(canonical_response, "thinking", None)
        )
    else:
        thinking = []

    if (
        text
        and not tool_calls
        and (detect_raw_envelope(text) or detect_raw_xml_tool_wrapper(text))
    ):
        sanitized_text = sanitize_envelope_leak(text)
        if sanitized_text != text:
            normalization["envelope_sanitized"] = True
            text = sanitized_text

    if not text and not tool_calls and effective_recover_empty_payload:
        text = effective_fallback_text.strip() or _EMPTY_PROVIDER_RESPONSE_TEXT
        finish_reason = finish_reason or "empty_payload_recovered"
        normalization["empty_payload_recovered"] = True

    normalization["response_normalized"] = True
    normalization["response_contract_version"] = PROVIDER_RESPONSE_INTERFACE_VERSION
    normalization["tool_calls_normalized"] = bool(tool_calls)
    normalization["normalization_profile"] = profile.name
    if tool_calls and "tool_parse_strategy" not in normalization:
        normalization["tool_parse_strategy"] = "native"
        normalization["tool_parse_format"] = "openai_native"

    return ProviderResponse(
        text=text,
        model=model,
        usage=usage,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        normalization=normalization,
        thinking=thinking,
    )


def _coerce_provider_response(raw_response: ProviderResponse | Any) -> ProviderResponse:
    if isinstance(raw_response, ProviderResponse):
        return raw_response

    if isinstance(raw_response, dict):
        getter = raw_response.get
        thinking = raw_response.get("thinking")
    elif _looks_like_response_object(raw_response):

        def getter(key: str, default: Any = None) -> Any:
            return getattr(raw_response, key, default)

        thinking = getattr(raw_response, "thinking", None)
    else:
        raise ProviderError(
            "Provider returned invalid response object type: "
            f"{type(raw_response).__name__}"
        )

    normalization = getter("normalization", {})
    return ProviderResponse(
        text=str(getter("text", "") or getter("output_text", "") or ""),
        model=str(getter("model", "") or ""),
        usage=getter("usage", {}),
        tool_calls=getter("tool_calls", []),
        finish_reason=str(getter("finish_reason", "") or ""),
        normalization=dict(normalization) if isinstance(normalization, dict) else {},
        thinking=_coerce_thinking_blocks(thinking),
    )


def _looks_like_response_object(value: Any) -> bool:
    return any(
        hasattr(value, key)
        for key in (
            "text",
            "output_text",
            "model",
            "usage",
            "tool_calls",
            "finish_reason",
        )
    )


def _thinking_passthrough_enabled() -> bool:
    """Resolve the ITE-06 operator-tunable passthrough flag.

    Reads through the shared env helper so the value is discoverable to
    env-config validation. Defaults to True (passthrough on).
    """

    from openminion.base.config.env import resolve_environment_config
    from openminion.modules.llm.constants import (
        PROVIDER_THINKING_PASSTHROUGH_DEFAULT,
        PROVIDER_THINKING_PASSTHROUGH_ENV,
    )

    env = resolve_environment_config()
    return env.get_bool(
        PROVIDER_THINKING_PASSTHROUGH_ENV,
        default=PROVIDER_THINKING_PASSTHROUGH_DEFAULT,
    )


def _coerce_thinking_blocks(raw: Any) -> list:
    """Coerce a raw `thinking` payload into thinking blocks."""

    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    # Lazy import keeps the dataclass dependency local to the helper.
    from openminion.modules.llm.providers.contracts import ThinkingBlock

    out: list = []
    for item in raw:
        if isinstance(item, ThinkingBlock):
            out.append(item)
            continue
        if isinstance(item, dict):
            try:
                out.append(
                    ThinkingBlock(
                        type="thinking",
                        content=str(item.get("content", "") or ""),
                        signature=(
                            str(item.get("signature"))
                            if item.get("signature") is not None
                            else None
                        ),
                        redacted=bool(item.get("redacted", False)),
                    )
                )
            except Exception:
                # Malformed entries are skipped, not fatal.
                continue
    return out


def _normalize_total_source(raw_usage: Any) -> str:
    value = _extract_usage_value(raw_usage, ("total_source", "total_tokens_source"))
    normalized = str(value or "").strip()
    return normalized if normalized in {"provider", "derived"} else ""


def _normalize_usage(raw_usage: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    usage_keys = {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
        "cached_tokens": ("cached_tokens", "cache_read_input_tokens"),
        "cache_creation_tokens": (
            "cache_creation_tokens",
            "cache_creation_input_tokens",
        ),
    }
    for key, aliases in usage_keys.items():
        value = _extract_usage_value(raw_usage, aliases)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            usage[key] = max(0, int(value))

    total_source = _normalize_total_source(raw_usage)
    if "total_tokens" in usage:
        usage["total_source"] = total_source or "provider"
    elif "prompt_tokens" in usage or "completion_tokens" in usage:
        usage["total_tokens"] = int(usage.get("prompt_tokens", 0)) + int(
            usage.get("completion_tokens", 0)
        )
        usage["total_source"] = "derived"
    return usage


def _extract_usage_value(raw_usage: Any, keys: Sequence[str]) -> Any:
    if isinstance(raw_usage, dict):
        for key in keys:
            if key in raw_usage:
                return raw_usage.get(key)
        return None

    for key in keys:
        if hasattr(raw_usage, key):
            return getattr(raw_usage, key)
    return None


def _normalize_tool_calls(raw_calls: Any) -> list[ProviderToolCall]:
    if not isinstance(raw_calls, (list, tuple)):
        return []

    normalized: list[ProviderToolCall] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_calls:
        call = _coerce_tool_call(item)
        if call is None:
            continue
        signature = (
            str(call.id or "").strip(),
            call.name,
            json.dumps(call.arguments, sort_keys=True, separators=(",", ":")),
            json.dumps(call.depends_on, sort_keys=True, separators=(",", ":")),
        )
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(call)
    return normalized


def _coerce_tool_call(raw_item: Any) -> ProviderToolCall | None:
    if isinstance(raw_item, ProviderToolCall):
        name = str(raw_item.name or "").strip()
        if not name:
            return None
        return ProviderToolCall(
            id=str(raw_item.id or "").strip(),
            name=name,
            arguments=_coerce_arguments(raw_item.arguments),
            source=str(raw_item.source or "native").strip() or "native",
            depends_on=_coerce_depends_on(getattr(raw_item, "depends_on", [])),
        )

    if isinstance(raw_item, dict):
        name = str(raw_item.get("name", "")).strip()
        if not name:
            return None
        return ProviderToolCall(
            id=str(raw_item.get("id", "")).strip(),
            name=name,
            arguments=_coerce_arguments(raw_item.get("arguments")),
            source=str(raw_item.get("source", "native")).strip() or "native",
            depends_on=_coerce_depends_on(raw_item.get("depends_on", [])),
        )

    if hasattr(raw_item, "name"):
        name = str(getattr(raw_item, "name", "")).strip()
        if not name:
            return None
        return ProviderToolCall(
            id=str(getattr(raw_item, "id", "")).strip(),
            name=name,
            arguments=_coerce_arguments(getattr(raw_item, "arguments", {})),
            source=(
                str(
                    getattr(raw_item, "source", "")
                    or getattr(raw_item, "status", "native")
                ).strip()
                or "native"
            ),
            depends_on=_coerce_depends_on(getattr(raw_item, "depends_on", [])),
        )

    return None


def _coerce_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if isinstance(raw_arguments, str):
        stripped = raw_arguments.strip()
        if not stripped:
            return {}
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _coerce_depends_on(raw_depends_on: Any) -> list[str]:
    if isinstance(raw_depends_on, str):
        candidate = str(raw_depends_on or "").strip()
        return [candidate] if candidate else []
    if not isinstance(raw_depends_on, (list, tuple, set)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_depends_on:
        dep = str(item or "").strip()
        if not dep or dep in seen:
            continue
        seen.add(dep)
        normalized.append(dep)
    return normalized


def _canonical_finish_reason(raw_value: str, *, aliases: dict[str, str]) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    key = value.lower()
    mapped = aliases.get(key)
    if mapped:
        return mapped
    return value
