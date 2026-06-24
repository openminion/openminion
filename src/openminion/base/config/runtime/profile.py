"""Runtime profile resolution and override helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from typing import Any, Mapping

from openminion.modules.llm.reasoning import (
    build_runtime_thinking_diagnostics,
)
from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.capability import (
    ModeRuntimeResolution,
    PluginRuntimeResolution,
    ProviderResolution,
    merge_tool_runtime_overrides,
    resolve_mode_runtime_policy,
    resolve_plugin_runtime_policy,
    resolve_provider_runtime_policy,
)
from ..core import AgentProfileConfig, OpenMinionConfig, resolve_agent_config


_PROVIDER_CONFIG_FIELDS: dict[str, str] = {
    "anthropic": "anthropic",
    "cerebras": "cerebras",
    "claude": "anthropic",
    "cortensor": "cortensor",
    "echo": "",
    "groq": "groq",
    "ollama": "ollama",
    "openai": "openai",
    "openrouter": "openrouter",
}
_SUPPORTED_OVERRIDE_FIELDS = (
    "override-provider",
    "override-model",
    "override-system-prompt",
    "override-thinking",
    "permission-mode",
    "permission-overrides",
)
_UNSUPPORTED_OVERRIDE_FIELDS = {
    "override-agent-name": "Phase 1 does not support runtime agent renaming.",
    "override-identity": "Phase 1 defers identity overrides to the identity owner lane.",
    "override-tool-policy": "Phase 1 defers tool-policy overrides to the policy owner lane.",
}


PERMISSION_MODE_DEFAULT = "default"
PERMISSION_MODE_READONLY = "readonly"
PERMISSION_MODE_BYPASS = "bypass"

PERMISSION_MODE_VALUES: frozenset[str] = frozenset(
    {PERMISSION_MODE_DEFAULT, PERMISSION_MODE_READONLY, PERMISSION_MODE_BYPASS}
)

PERMISSION_MODE_CYCLE: tuple[str, ...] = (
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_READONLY,
    PERMISSION_MODE_BYPASS,
)


def next_permission_mode(current: str) -> str:
    """Cycle default -> readonly -> bypass -> default."""
    current_norm = (current or "").strip().lower()
    if current_norm not in PERMISSION_MODE_VALUES:
        return PERMISSION_MODE_DEFAULT
    idx = PERMISSION_MODE_CYCLE.index(current_norm)
    return PERMISSION_MODE_CYCLE[(idx + 1) % len(PERMISSION_MODE_CYCLE)]


@dataclass(frozen=True)
class RunProfileOverrides:
    provider: str = ""
    model: str = ""
    system_prompt: str = ""
    thinking: str = ""
    permission_mode: str = ""
    permission_overrides: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.provider
            or self.model
            or self.system_prompt
            or self.thinking
            or self.permission_mode
            or self.permission_overrides
        )

    def cache_key(self) -> str:
        if self.is_empty():
            return "none"
        return "|".join(
            (
                self.provider or "-",
                self.model or "-",
                self.system_prompt or "-",
                self.thinking or "-",
                self.permission_mode or "-",
                _permission_overrides_cache_key(self.permission_overrides),
            )
        )


def combine_run_profile_overrides(
    base: RunProfileOverrides | None,
    extra: RunProfileOverrides | None,
) -> RunProfileOverrides:
    base_overrides = base or RunProfileOverrides()
    extra_overrides = extra or RunProfileOverrides()
    return RunProfileOverrides(
        provider=extra_overrides.provider or base_overrides.provider,
        model=extra_overrides.model or base_overrides.model,
        system_prompt=extra_overrides.system_prompt or base_overrides.system_prompt,
        thinking=extra_overrides.thinking or base_overrides.thinking,
        permission_mode=(
            extra_overrides.permission_mode or base_overrides.permission_mode
        ),
        permission_overrides=(
            extra_overrides.permission_overrides or base_overrides.permission_overrides
        ),
    )


def run_profile_overrides_from_mapping(
    payload: Mapping[str, Any] | None,
) -> RunProfileOverrides:
    if payload is None:
        return RunProfileOverrides()

    _reject_unsupported_override_keys(payload)
    provider = _normalized_override_value(
        payload, "override_provider", "override-provider"
    )
    model = _normalized_override_value(payload, "override_model", "override-model")
    system_prompt = _normalized_override_value(
        payload,
        "override_system_prompt",
        "override-system-prompt",
    )
    thinking = _normalized_override_value(
        payload,
        "override_thinking",
        "override-thinking",
    )
    permission_mode = _normalized_override_value(
        payload,
        "permission_mode",
        "permission-mode",
        "override_permission_mode",
        "override-permission-mode",
    )
    if permission_mode:
        permission_mode = permission_mode.strip().lower()
        if permission_mode not in PERMISSION_MODE_VALUES:
            valid = ", ".join(sorted(PERMISSION_MODE_VALUES))
            raise ConfigError(
                f"Unsupported permission mode {permission_mode!r}. "
                f"Supported permission modes: {valid}."
            )
        if permission_mode == PERMISSION_MODE_DEFAULT:
            permission_mode = ""
    permission_overrides = _normalized_permission_overrides(
        payload.get("permission_overrides")
        or payload.get("permission-overrides")
        or payload.get("override_permission_overrides")
        or payload.get("override-permission-overrides")
    )
    if provider:
        _provider_config_field_name(provider)
    return RunProfileOverrides(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        thinking=thinking,
        permission_mode=permission_mode,
        permission_overrides=permission_overrides,
    )


def resolve_runtime_profile(
    config: OpenMinionConfig,
    *,
    agent_id: str | None = None,
    overrides: RunProfileOverrides | None = None,
) -> AgentProfileConfig:
    """Return the effective :class:`AgentProfileConfig` for *agent_id*."""

    selected = resolve_agent_config(config, agent_id)
    effective_overrides = overrides or RunProfileOverrides()
    provider_resolution = resolve_provider_runtime_policy(
        system_policy=config.runtime.provider_policy,
        agent_policy=selected.provider_policy,
        code_default_provider="echo",
        legacy_agent_provider=str(selected.provider or ""),
        invocation_provider=effective_overrides.provider,
    )
    mode_resolution = resolve_mode_runtime_policy(
        system_modes=config.runtime.modes,
        agent_modes=selected.modes,
    )
    thinking_diagnostics = build_runtime_thinking_diagnostics(
        code_default_profile="minimal",
        system_profile=_thinking_policy_profile(config.runtime.thinking_policy),
        agent_profile=_selected_agent_thinking_profile(selected),
        invocation_requested_profile=effective_overrides.thinking or None,
        provider_name=provider_resolution.selected_provider,
        model_name=effective_overrides.model,
        purpose="runtime_profile",
    )
    merged_tools = merge_tool_runtime_overrides(
        system_tools=config.runtime.tools,
        agent_tools=selected.tools,
    )
    return replace(
        selected,
        provider=provider_resolution.selected_provider,
        system_prompt=effective_overrides.system_prompt or selected.system_prompt,
        thinking=thinking_diagnostics.effective.reasoning_profile,
        modes=mode_resolution.effective_modes,
        tools=merged_tools,
    )


def build_runtime_config(
    config: OpenMinionConfig,
    *,
    agent_id: str | None = None,
    overrides: RunProfileOverrides | None = None,
) -> OpenMinionConfig:
    effective_overrides = overrides or RunProfileOverrides()
    selected_profile = resolve_agent_config(config, agent_id)
    selected_agent_id = str(agent_id or "").strip()
    effective_profile = resolve_runtime_profile(
        config,
        agent_id=agent_id,
        overrides=effective_overrides,
    )
    plugin_resolution = resolve_plugin_runtime_policy(
        compatibility_enabled_plugins=list(
            getattr(config, "enabled_plugins", []) or []
        ),
        system_policy=config.runtime.plugins,
    )
    merged_tss_value, merged_tss_has = _pick_flag(
        agent_has=effective_profile.has_tool_schema_shortlisting_enabled,
        agent_value=effective_profile.tool_schema_shortlisting_enabled,
        runtime_has=config.runtime.has_tool_schema_shortlisting_enabled,
        runtime_value=config.runtime.tool_schema_shortlisting_enabled,
    )
    merged_bwa_value, merged_bwa_has = _pick_flag(
        agent_has=effective_profile.has_allow_background_write_authorization,
        agent_value=effective_profile.allow_background_write_authorization,
        runtime_has=config.runtime.has_allow_background_write_authorization,
        runtime_value=config.runtime.allow_background_write_authorization,
    )
    merged_variant, merged_variant_has = _merge_trailer_variant(
        agent_variant=effective_profile.trailer_guidance_variant,
        runtime_variant=config.runtime.trailer_guidance_variant,
        agent_has=effective_profile.has_trailer_guidance_variant,
        runtime_has=config.runtime.has_trailer_guidance_variant,
    )
    new_agents = dict(config.agents)
    effective_id = selected_agent_id or str(effective_profile.name or "").strip()
    if effective_id and effective_id in new_agents:
        new_agents[effective_id] = effective_profile
    effective_config = replace(
        config,
        default_agent=(
            selected_agent_id
            if selected_agent_id and selected_agent_id in new_agents
            else config.default_agent
        ),
        agents=new_agents,
        runtime=replace(
            config.runtime,
            tool_schema_shortlisting_enabled=merged_tss_value,
            has_tool_schema_shortlisting_enabled=merged_tss_has,
            allow_background_write_authorization=merged_bwa_value,
            has_allow_background_write_authorization=merged_bwa_has,
            trailer_guidance_variant=(
                dict(merged_variant or {}) if merged_variant_has else None
            ),
            has_trailer_guidance_variant=merged_variant_has,
            tools=merge_tool_runtime_overrides(
                system_tools=config.runtime.tools,
                agent_tools=effective_profile.tools,
            ),
        ),
        enabled_plugins=list(plugin_resolution.effective_enabled),
    )

    provider_config_overrides = _select_profile_provider_config_overrides(
        selected_profile,
        effective_profile,
        effective_overrides,
    )
    effective_model_override = (
        effective_overrides.model
        or str(provider_config_overrides.get("model", "") or "").strip()
    )
    if not provider_config_overrides and not effective_model_override:
        return effective_config

    provider_field = _provider_config_field_name(effective_profile.provider)
    if not provider_field:
        raise ConfigError(
            "Runtime model overrides are not supported for the echo provider."
        )
    provider_config = getattr(config.providers, provider_field, None)
    if provider_config is None:
        raise ConfigError(
            f"Runtime model override is not supported for provider {effective_profile.provider!r}."
        )
    provider_patch = dict(provider_config_overrides)
    if effective_model_override:
        provider_patch["model"] = effective_model_override
    updated_provider = _apply_provider_config_patch(
        provider_name=effective_profile.provider,
        provider_config=provider_config,
        patch=provider_patch,
    )
    updated_providers = replace(config.providers, **{provider_field: updated_provider})
    return replace(effective_config, providers=updated_providers)


def build_capability_runtime_diagnostics(
    config: OpenMinionConfig,
    *,
    agent_id: str | None = None,
    overrides: RunProfileOverrides | None = None,
) -> dict[str, Any]:
    selected = resolve_agent_config(config, agent_id)
    effective_overrides = overrides or RunProfileOverrides()
    provider_resolution = resolve_provider_runtime_policy(
        system_policy=config.runtime.provider_policy,
        agent_policy=selected.provider_policy,
        code_default_provider="echo",
        legacy_agent_provider=str(selected.provider or ""),
        invocation_provider=effective_overrides.provider,
    )
    mode_resolution = resolve_mode_runtime_policy(
        system_modes=config.runtime.modes,
        agent_modes=selected.modes,
    )
    plugin_resolution = resolve_plugin_runtime_policy(
        compatibility_enabled_plugins=list(
            getattr(config, "enabled_plugins", []) or []
        ),
        system_policy=config.runtime.plugins,
    )
    thinking_diagnostics = build_runtime_thinking_diagnostics(
        code_default_profile="minimal",
        system_profile=_thinking_policy_profile(config.runtime.thinking_policy),
        agent_profile=_selected_agent_thinking_profile(selected),
        invocation_requested_profile=effective_overrides.thinking or None,
        provider_name=provider_resolution.selected_provider,
        model_name=effective_overrides.model,
        purpose="runtime_diagnostics",
    )
    brain_payload = _brain_diagnostic_payload(selected, config.runtime)
    from openminion.base.config.runtime.tools import tool_runtime_config_to_dict

    merged_tools = merge_tool_runtime_overrides(
        system_tools=config.runtime.tools,
        agent_tools=selected.tools,
    )
    tools_payload = tool_runtime_config_to_dict(merged_tools)
    return {
        "provider": _provider_resolution_to_dict(provider_resolution),
        "thinking": _thinking_diagnostics_to_dict(thinking_diagnostics),
        "modes": _mode_resolution_to_dict(mode_resolution),
        "plugins": _plugin_resolution_to_dict(plugin_resolution),
        "brain": brain_payload,
        "tools": tools_payload,
    }


def _brain_diagnostic_payload(
    selected: AgentProfileConfig,
    runtime: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if selected.has_tool_schema_shortlisting_enabled:
        payload["tool_schema_shortlisting_enabled"] = bool(
            selected.tool_schema_shortlisting_enabled
        )
    elif runtime.has_tool_schema_shortlisting_enabled:
        payload["tool_schema_shortlisting_enabled"] = bool(
            runtime.tool_schema_shortlisting_enabled
        )
    if selected.has_allow_background_write_authorization:
        payload["allow_background_write_authorization"] = bool(
            selected.allow_background_write_authorization
        )
    elif runtime.has_allow_background_write_authorization:
        payload["allow_background_write_authorization"] = bool(
            runtime.allow_background_write_authorization
        )
    if selected.has_trailer_guidance_variant or runtime.has_trailer_guidance_variant:
        merged: dict[str, str] = {}
        if runtime.has_trailer_guidance_variant:
            merged.update(runtime.trailer_guidance_variant or {})
        if selected.has_trailer_guidance_variant:
            merged.update(selected.trailer_guidance_variant or {})
        payload["trailer_guidance_variant"] = merged
    return payload


def _pick_flag(
    *,
    agent_has: bool,
    agent_value: Any,
    runtime_has: bool,
    runtime_value: Any,
) -> tuple[Any, bool]:
    if agent_has:
        return agent_value, True
    if runtime_has:
        return runtime_value, True
    return None, False


def _merge_trailer_variant(
    *,
    agent_variant: dict[str, str] | None,
    runtime_variant: dict[str, str] | None,
    agent_has: bool,
    runtime_has: bool,
) -> tuple[dict[str, str] | None, bool]:
    if not agent_has and not runtime_has:
        return None, False
    merged: dict[str, str] = {}
    if runtime_has:
        merged.update(runtime_variant or {})
    if agent_has:
        merged.update(agent_variant or {})
    return merged, True


def _reject_unsupported_override_keys(payload: Mapping[str, Any]) -> None:
    for key, reason in _UNSUPPORTED_OVERRIDE_FIELDS.items():
        value = _normalized_override_value(payload, key, key.replace("-", "_"))
        if value:
            raise ConfigError(
                f"Unsupported runtime override {key!r}. {reason} "
                f"Supported override fields: {', '.join(_SUPPORTED_OVERRIDE_FIELDS)}."
            )


def _normalized_override_value(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalized_permission_overrides(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    payload: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "permission_overrides must be a JSON object mapping tool names to modes."
            ) from exc
    if not isinstance(payload, Mapping):
        raise ConfigError(
            "permission_overrides must be a mapping from tool name to permission mode."
        )
    normalized: dict[str, str] = {}
    valid_modes = {"ask", "auto", "bypass", "readonly"}
    for raw_tool_name, raw_mode in payload.items():
        tool_name = str(raw_tool_name or "").strip().lower()
        if not tool_name:
            continue
        mode = str(raw_mode or "").strip().lower()
        if mode in {"default", "plan"}:
            mode = "ask"
        elif mode in {"acceptedits"}:
            mode = "auto"
        elif mode in {"bypasspermissions"}:
            mode = "bypass"
        elif mode in {"read_only", "read-only"}:
            mode = "readonly"
        if mode not in valid_modes:
            valid = ", ".join(sorted(valid_modes))
            raise ConfigError(
                f"Unsupported per-tool permission mode {raw_mode!r} for "
                f"{tool_name!r}. Supported modes: {valid}."
            )
        normalized[tool_name] = mode
    return tuple(sorted(normalized.items()))


def _permission_overrides_cache_key(overrides: tuple[tuple[str, str], ...]) -> str:
    if not overrides:
        return "-"
    return ",".join(f"{tool}:{mode}" for tool, mode in overrides)


def _normalize_provider_name(raw: str) -> str:
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return "echo"
    _provider_config_field_name(normalized)
    return normalized


def _provider_config_field_name(provider_name: str) -> str:
    normalized = str(provider_name or "").strip().lower()
    field_name = _PROVIDER_CONFIG_FIELDS.get(normalized)
    if field_name is None:
        valid = ", ".join(repr(name) for name in sorted(_PROVIDER_CONFIG_FIELDS))
        raise ConfigError(
            f"Unknown runtime override provider {provider_name!r}. Valid providers: {valid}."
        )
    return field_name


def _select_profile_provider_config_overrides(
    selected_profile: AgentProfileConfig,
    effective_profile: AgentProfileConfig,
    effective_overrides: RunProfileOverrides,
) -> dict[str, Any]:
    if effective_overrides.provider:
        return {}
    selected_provider = str(selected_profile.provider or "").strip().lower()
    effective_provider = str(effective_profile.provider or "").strip().lower()
    if not selected_provider or selected_provider != effective_provider:
        return {}
    return dict(getattr(selected_profile, "provider_config_overrides", {}) or {})


def _apply_provider_config_patch(
    *,
    provider_name: str,
    provider_config: Any,
    patch: Mapping[str, Any],
) -> Any:
    if not patch:
        return provider_config
    allowed_fields = set(asdict(provider_config))
    unknown_fields = sorted(set(patch) - allowed_fields)
    if unknown_fields:
        valid = ", ".join(sorted(allowed_fields))
        raise ConfigError(
            f"Unsupported provider_config_overrides for provider {provider_name!r}: "
            f"{unknown_fields!r}. Valid fields: {valid}."
        )
    return replace(provider_config, **dict(patch))


def _thinking_policy_profile(policy: Any | None) -> str | None:
    if policy is None:
        return None
    reasoning_profile = str(getattr(policy, "reasoning_profile", "") or "").strip()
    return reasoning_profile or None


def _selected_agent_thinking_profile(selected: AgentProfileConfig) -> str | None:
    override_profile = _thinking_policy_profile(selected.thinking_policy)
    if override_profile:
        return override_profile
    legacy = str(getattr(selected, "thinking", "") or "").strip()
    return legacy or None


def _provider_resolution_to_dict(resolution: ProviderResolution) -> dict[str, Any]:
    return {
        "selected": resolution.selected_provider,
        "source": resolution.source,
        "effective_enabled": list(resolution.effective_enabled),
        "provider_order": list(resolution.provider_order),
        "system_default_provider": resolution.system_default_provider,
        "agent_default_provider": resolution.agent_default_provider,
        "invocation_requested_provider": resolution.invocation_requested_provider,
    }


def _thinking_diagnostics_to_dict(diagnostics: Any) -> dict[str, Any]:
    return {
        "code_default_profile": diagnostics.code_default_profile,
        "system_profile": diagnostics.system_profile,
        "agent_profile": diagnostics.agent_profile,
        "invocation_requested_profile": diagnostics.invocation_requested_profile,
        "effective": diagnostics.effective.diagnostics_payload(),
    }


def _mode_resolution_to_dict(resolution: ModeRuntimeResolution) -> dict[str, Any]:
    return {
        "effective": {
            name: {"enabled": bool(config.enabled)}
            for name, config in sorted(
                resolution.effective_modes.items(), key=lambda item: item[0]
            )
        },
        "blocked_reasons": dict(resolution.blocked_reasons),
    }


def _plugin_resolution_to_dict(resolution: PluginRuntimeResolution) -> dict[str, Any]:
    return {
        "effective_enabled": list(resolution.effective_enabled),
        "blocked": list(resolution.blocked),
        "source": resolution.source,
    }


__all__ = [
    "RunProfileOverrides",
    "build_capability_runtime_diagnostics",
    "build_runtime_config",
    "combine_run_profile_overrides",
    "resolve_runtime_profile",
    "run_profile_overrides_from_mapping",
]
