"""Post-tool follow-up provider calls."""

from typing import Any

from openminion.modules.llm.contracts import (
    ProviderResponse,
    ProviderToolSpec,
    extract_fallback_tool_calls_from_text_with_metadata,
)
from openminion.modules.tool.exposure import get_allowed_model_tool_names


def _provider_specs(tools: Any) -> list[Any]:
    for accessor_name in ("model_provider_specs", "provider_specs"):
        accessor = getattr(tools, accessor_name, None)
        if not callable(accessor):
            continue
        try:
            return list(accessor())
        except Exception:
            continue
    return []


def _provider_name(runner: Any) -> str:
    return str(
        getattr(
            getattr(getattr(runner, "service_port", None), "provider", None),
            "name",
            "",
        )
        or ""
    ).strip()


def available_follow_up_tools(runner: Any) -> list[ProviderToolSpec]:
    tools = getattr(getattr(runner, "service_port", None), "tools", None)
    if tools is None:
        return []
    return [
        spec for spec in _provider_specs(tools) if isinstance(spec, ProviderToolSpec)
    ]


def recover_text_tool_calls(
    runner: Any,
    *,
    response: ProviderResponse,
) -> ProviderResponse:
    if response.tool_calls:
        return response
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        return response
    tools = getattr(getattr(runner, "service_port", None), "tools", None)
    allowed_tool_names = None
    if tools is not None:
        names = sorted(get_allowed_model_tool_names(tools))
        if names:
            allowed_tool_names = names
    parsed_calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        text,
        provider_name=_provider_name(runner),
        model_name=str(getattr(response, "model", "") or "").strip(),
        allowed_tool_names=allowed_tool_names,
    )
    if not parsed_calls:
        return response
    normalization = dict(getattr(response, "normalization", {}) or {})
    normalization.update(dict(metadata or {}))
    return ProviderResponse(
        text="",
        model=str(getattr(response, "model", "") or ""),
        usage=dict(getattr(response, "usage", {}) or {}),
        tool_calls=list(parsed_calls),
        finish_reason="tool_calls",
        normalization=normalization,
        thinking=list(getattr(response, "thinking", []) or []),
    )
