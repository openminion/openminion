"""Agent prompt, history, and tool-feedback adapters."""

from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderHistoryMessage, LLMProvider
from openminion.modules.llm.providers.tool_calling import (
    detect_raw_envelope,
    detect_raw_tool_markup,
)

_IDENTITY_FRAME = (
    "## Your Identity\n\n"
    "You are the agent described below. Apply this persona to all responses — "
    "voice, tone, and name — not only when directly asked about yourself. "
    "Do not describe yourself using information outside this profile.\n\n"
)


def _history_role(role: str) -> str:
    return {
        "user": "user",
        "inbound": "user",
        "assistant": "assistant",
        "outbound": "assistant",
    }.get(str(role).lower(), "system")


def _loop_tool_feedback(tool_results: list[Any], max_chars: int | None = None) -> str:
    text = "\n\n".join(str(res) for res in tool_results)
    if max_chars is None:
        return text
    limit = max(1, int(max_chars))
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _map_history_to_provider(history: list[Message]) -> list[ProviderHistoryMessage]:
    return [
        ProviderHistoryMessage(
            role=_history_role(message.metadata.get("role") or "user"),
            content=message.body,
        )
        for message in history
    ]


def _looks_like_tool_call_envelope_text(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    return (
        "unexecutable_tool_envelope" in candidate.lower()
        or detect_raw_envelope(candidate)
        or detect_raw_tool_markup(candidate)
        or candidate.startswith("{")
        and ("tool_calls" in candidate or '"name"' in candidate)
    )


def _provider_tool_call_strategy(
    provider_or_config: LLMProvider | OpenMinionConfig,
    config: OpenMinionConfig | None = None,
) -> str:
    provider: LLMProvider | None = None
    cfg: OpenMinionConfig
    if config is None:
        cfg = provider_or_config  # type: ignore[assignment]
    else:
        provider = provider_or_config  # type: ignore[assignment]
        cfg = config

    provider_strategy = str(getattr(provider, "tool_call_strategy", "") or "").strip()
    if provider_strategy:
        return provider_strategy

    from openminion.base.config.core import resolve_default_agent_id as _rda

    try:
        _default_agent_id = _rda(cfg)
        _default_profile = cfg.agents.get(_default_agent_id)
    except Exception:
        _default_profile = None
    provider_name = str(getattr(_default_profile, "provider", "") or "").strip().lower()
    providers_cfg = getattr(cfg, "providers", None)
    provider_cfg = (
        getattr(providers_cfg, provider_name, None) if providers_cfg else None
    )
    config_strategy = str(getattr(provider_cfg, "tool_call_strategy", "") or "").strip()
    if config_strategy:
        return config_strategy

    return "hybrid"


def _resolve_system_prompt(config: OpenMinionConfig | str) -> str:
    if isinstance(config, str):
        prompt = config.strip()
        return prompt or "You are a helpful assistant."
    from openminion.base.config.core import resolve_default_agent_id

    try:
        default_agent_id = resolve_default_agent_id(config)
    except Exception:  # noqa: BLE001
        return "You are a helpful assistant."
    profile = config.agents.get(default_agent_id)
    if profile is not None and profile.system_prompt:
        return profile.system_prompt
    return "You are a helpful assistant."
