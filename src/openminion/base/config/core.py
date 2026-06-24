"""Core OpenMinion config dataclasses and profile resolution."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from openminion.base.config.base import DEFAULT_STORAGE_PATH, UnknownProfileError
from openminion.base.config.parse import _normalize_brain_integration_mode

from .providers import ProvidersConfig
from .skill_selection import skill_value_to_payload
from .runtime.capability import (
    ModeRuntimePolicyConfig,
    PluginRuntimePolicyConfig,
    ProviderRuntimePolicyConfig,
    ThinkingRuntimePolicyConfig,
    coerce_mode_runtime_policy_map,
    coerce_plugin_runtime_policy_config,
    coerce_provider_runtime_policy_config,
    coerce_thinking_runtime_policy_config,
    mode_runtime_policy_to_dict,
    plugin_runtime_policy_to_dict,
    provider_runtime_policy_to_dict,
    thinking_runtime_policy_to_dict,
)
from .runtime.tools import (
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
    tool_runtime_config_to_dict,
)
from .runtime import (
    ContextConfig,
    IdentityConfig,
    RuntimeConfig,
    SelfImprovementConfig,
    ToolPolicyConfig,
)
from .mcp import (
    MCPExposureConfig,
    coerce_mcp_exposure_config,
    mcp_exposure_config_to_dict,
)


@dataclass
class ActionPolicyMatchConfig:
    tool_category: str = ""
    tool_name: str = ""
    min_risk_class: str = ""


@dataclass
class ActionPolicyRuleConfig:
    match: ActionPolicyMatchConfig = field(default_factory=ActionPolicyMatchConfig)
    mode: str = "ask"


@dataclass
class ActionPolicyConfig:
    mode: str = "auto"
    default_action: str = "require_confirm"
    allow_read_only_without_prompt: bool = True
    rules: list[ActionPolicyRuleConfig] = field(default_factory=list)
    affirmative_tokens: list[str] = field(
        default_factory=lambda: [
            "yes",
            "y",
            "proceed",
            "go",
            "confirm",
            "sure",
            "affirmative",
            "sounds good",
        ]
    )
    negative_tokens: list[str] = field(
        default_factory=lambda: ["no", "n", "cancel", "stop", "abort", "not now"]
    )


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 18789
    api_turn_timeout_seconds: int = 45
    brain_integration_mode: str = "contextctl_authoritative"

    def __post_init__(self) -> None:
        self.brain_integration_mode = _normalize_brain_integration_mode(
            self.brain_integration_mode
        )


@dataclass
class ChannelPolicyConfig:
    dm_policy: str = "pairing"
    group_policy: str = "disabled"
    dm_allowlist: list[str] = field(default_factory=list)
    group_allowlist: list[str] = field(default_factory=list)
    paired_dm_senders: list[str] = field(default_factory=list)


@dataclass
class ChannelAuthenticityConfig:
    mode: str = "warn"
    trusted_channels: list[str] = field(default_factory=lambda: ["console"])
    required_channels: list[str] = field(default_factory=list)
    secret_env_by_channel: dict[str, str] = field(default_factory=dict)
    max_age_seconds: int = 300
    allowed_algorithms: list[str] = field(default_factory=lambda: ["hmac-sha256"])


@dataclass
class SecurityConfig:
    tool_policy: ToolPolicyConfig = field(default_factory=ToolPolicyConfig)


@dataclass
class AgentProfileConfig:
    """Per-agent profile configuration layered by `resolve_agent_config`."""

    name: str = ""
    default_channel: str = ""
    thinking: str = ""
    provider: str = ""
    default_act_profile: str = ""
    skill: str | list[str] | None = None
    skill_catalog: list[str] = field(default_factory=list)
    skill_explicit: bool = False
    skill_catalog_explicit: bool = False
    system_prompt: str = ""
    provider_config_overrides: dict[str, Any] = field(default_factory=dict)
    model_capability_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    action_policy: ActionPolicyConfig | None = None

    tool_schema_shortlisting_enabled: bool | None = None
    has_tool_schema_shortlisting_enabled: bool = field(default=False, repr=False)
    allow_background_write_authorization: bool | None = None
    has_allow_background_write_authorization: bool = field(default=False, repr=False)
    trailer_guidance_variant: dict[str, str] | None = None
    has_trailer_guidance_variant: bool = field(default=False, repr=False)

    thinking_policy: ThinkingRuntimePolicyConfig | None = None
    provider_policy: ProviderRuntimePolicyConfig | None = None

    modes: dict[str, ModeRuntimePolicyConfig] = field(default_factory=dict)
    plugins: PluginRuntimePolicyConfig | None = None
    tools: ToolRuntimeConfig = field(default_factory=ToolRuntimeConfig)
    mcp_exposure: MCPExposureConfig = field(default_factory=MCPExposureConfig)

    def __post_init__(self) -> None:
        self.thinking_policy = coerce_thinking_runtime_policy_config(
            self.thinking_policy,
            field_path="agents.<id>.thinking_policy",
        )
        self.provider_policy = coerce_provider_runtime_policy_config(
            self.provider_policy,
            field_path="agents.<id>.provider_policy",
        )
        self.modes = coerce_mode_runtime_policy_map(
            self.modes,
            field_path="agents.<id>.modes",
        )
        self.plugins = coerce_plugin_runtime_policy_config(
            self.plugins,
            field_path="agents.<id>.plugins",
        )
        self.tools = coerce_tool_runtime_config(self.tools)
        self.mcp_exposure = coerce_mcp_exposure_config(self.mcp_exposure)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "default_channel": self.default_channel,
            "thinking": self.thinking,
            "provider": self.provider,
            "system_prompt": self.system_prompt,
        }
        if str(self.default_act_profile or "").strip():
            payload["default_act_profile"] = str(self.default_act_profile).strip()
        if self.skill_explicit:
            payload["skill"] = skill_value_to_payload(self.skill)
        if self.skill_catalog_explicit:
            payload["skill_catalog"] = list(self.skill_catalog)
        if self.provider_config_overrides:
            payload["provider_config_overrides"] = dict(self.provider_config_overrides)
        if self.model_capability_overrides:
            payload["model_capability_overrides"] = dict(
                self.model_capability_overrides
            )
        if self.action_policy is not None:
            payload["action_policy"] = {
                "mode": str(self.action_policy.mode).strip().lower() or "auto",
                "default_action": (
                    str(self.action_policy.default_action).strip().lower()
                    or "require_confirm"
                ),
                "allow_read_only_without_prompt": bool(
                    self.action_policy.allow_read_only_without_prompt
                ),
                "rules": [
                    {
                        "match": {
                            "tool_category": rule.match.tool_category,
                            "tool_name": rule.match.tool_name,
                            "min_risk_class": rule.match.min_risk_class,
                        },
                        "mode": str(rule.mode).strip().lower() or "ask",
                    }
                    for rule in self.action_policy.rules
                ],
                "affirmative_tokens": list(self.action_policy.affirmative_tokens),
                "negative_tokens": list(self.action_policy.negative_tokens),
            }
        if self.has_tool_schema_shortlisting_enabled:
            payload["tool_schema_shortlisting_enabled"] = bool(
                self.tool_schema_shortlisting_enabled
            )
        if self.has_allow_background_write_authorization:
            payload["allow_background_write_authorization"] = bool(
                self.allow_background_write_authorization
            )
        if self.has_trailer_guidance_variant:
            payload["trailer_guidance_variant"] = dict(
                self.trailer_guidance_variant or {}
            )
        thinking_policy_payload = thinking_runtime_policy_to_dict(self.thinking_policy)
        if thinking_policy_payload:
            payload["thinking_policy"] = thinking_policy_payload
        provider_policy_payload = provider_runtime_policy_to_dict(self.provider_policy)
        if provider_policy_payload:
            payload["provider_policy"] = provider_policy_payload
        modes_payload = mode_runtime_policy_to_dict(self.modes)
        if modes_payload:
            payload["modes"] = modes_payload
        plugins_payload = plugin_runtime_policy_to_dict(self.plugins)
        if plugins_payload:
            payload["plugins"] = plugins_payload
        tools_payload = tool_runtime_config_to_dict(self.tools)
        if tools_payload:
            payload["tools"] = tools_payload
        mcp_exposure_payload = mcp_exposure_config_to_dict(self.mcp_exposure)
        if mcp_exposure_payload:
            payload["mcp_exposure"] = mcp_exposure_payload
        return payload


@dataclass(frozen=True)
class AgentIdentityResolution:
    public_agent_id: str
    profile: AgentProfileConfig


@dataclass
class VectorConfig:
    enabled: bool = False
    provider: str = "local"
    model: str = "all-MiniLM-L6-v2"
    dimension: int = 384
    sync_batch_size: int = 32
    search_k: int = 10


class ConfigValidationError(ValueError):
    """Raised when operator-supplied configuration is invalid."""


@dataclass
class StorageConfig:
    path: str = str(DEFAULT_STORAGE_PATH)
    backend: str = "sqlite"
    postgres_url: str = ""
    postgres_pool_min: int = 1
    postgres_pool_max: int = 5

    def __post_init__(self) -> None:
        allowed = {"sqlite", "postgres"}
        if self.backend not in allowed:
            raise ConfigValidationError(
                f"storage.backend must be one of {sorted(allowed)!r}, got {self.backend!r}"
            )
        if self.backend == "postgres" and not self.postgres_url.strip():
            raise ConfigValidationError(
                "storage.postgres_url must be non-empty when storage.backend is 'postgres'"
            )

    def record_backend(self) -> str:
        return "record.postgres" if self.backend == "postgres" else "record.sqlite"

    def record_backend_options(self) -> dict[str, object]:
        if self.backend == "postgres":
            return {
                "url": self.postgres_url,
                "pool_min": self.postgres_pool_min,
                "pool_max": self.postgres_pool_max,
            }
        return {}


@dataclass
class OpenMinionConfig:
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    channel_policy: ChannelPolicyConfig = field(default_factory=ChannelPolicyConfig)
    channel_authenticity: ChannelAuthenticityConfig = field(
        default_factory=ChannelAuthenticityConfig
    )
    security: SecurityConfig = field(default_factory=SecurityConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    vector: VectorConfig = field(default_factory=VectorConfig)
    self_improvement: SelfImprovementConfig = field(
        default_factory=SelfImprovementConfig
    )
    context: ContextConfig = field(default_factory=ContextConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    action_policy: ActionPolicyConfig = field(default_factory=ActionPolicyConfig)
    agents: dict[str, AgentProfileConfig] = field(default_factory=dict)
    default_agent: str = ""
    enabled_channels: list[str] = field(default_factory=lambda: ["console"])
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    enabled_plugins: list[str] = field(default_factory=lambda: ["validate"])
    module_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OpenMinionConfig":
        from .parser import openminion_config_from_dict

        return openminion_config_from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        from .parser import openminion_config_to_dict

        return openminion_config_to_dict(self)


def resolve_default_agent_id(config: OpenMinionConfig) -> str:
    """Return the default agent id, requiring explicit choice for multi-agent configs."""

    if not config.agents:
        raise ConfigValidationError(
            "OpenMinionConfig.agents is empty; no default agent is available. "
            "Post-CSC configs must populate the 'agents' catalog. "
            "See the config-shape migration guide."
        )
    if len(config.agents) == 1:
        return next(iter(config.agents))
    normalized = str(config.default_agent or "").strip()
    if not normalized:
        valid = ", ".join(repr(k) for k in sorted(config.agents))
        raise UnknownProfileError(
            f"multi-agent config requires explicit 'default_agent' "
            f"naming one of: {valid}."
        )
    if normalized not in config.agents:
        valid = ", ".join(repr(k) for k in sorted(config.agents))
        raise UnknownProfileError(
            f"default_agent={normalized!r} is not present in the agents "
            f"catalog. Valid profiles: {valid}."
        )
    return normalized


def configured_agent_ids(config: OpenMinionConfig) -> list[str]:
    """Return the deterministic set of selectable profile ids for *config*."""
    return sorted(config.agents.keys())


def _merge_trailer_guidance_variant(
    *,
    agent_variant: dict[str, str] | None,
    runtime_variant: dict[str, str] | None,
    agent_set: bool,
    runtime_set: bool,
) -> tuple[dict[str, str] | None, bool]:
    """Shallow-merge trailer_guidance_variant dicts: agent wins per-key."""
    if not agent_set and not runtime_set:
        return None, False
    merged: dict[str, str] = {}
    if runtime_set:
        merged.update(runtime_variant or {})
    if agent_set:
        merged.update(agent_variant or {})
    return merged, True


def resolve_agent_config(
    config: OpenMinionConfig, agent_id: str | None = None
) -> AgentProfileConfig:
    """Resolve the effective :class:`AgentProfileConfig` for *agent_id*."""

    requested_agent_id = str(agent_id or "").strip()
    if not config.agents:
        raise UnknownProfileError(
            "No agent profiles are configured; post-CSC configs must populate "
            "the 'agents' catalog. "
            "See the config-shape migration guide."
        )

    if requested_agent_id:
        if requested_agent_id not in config.agents:
            valid = ", ".join(repr(k) for k in sorted(config.agents))
            raise UnknownProfileError(
                f"Unknown agent profile {requested_agent_id!r}. "
                f"Valid profiles: {valid}."
            )
        selected_id = requested_agent_id
    else:
        selected_id = resolve_default_agent_id(config)

    profile = config.agents[selected_id]
    runtime = config.runtime

    # Shallow-merge per field: profile explicit → runtime explicit → default.
    def _pick_bool_flag(attr_name: str) -> tuple[bool | None, bool]:
        profile_has = getattr(profile, f"has_{attr_name}")
        runtime_has = getattr(runtime, f"has_{attr_name}")
        if profile_has:
            return getattr(profile, attr_name), True
        if runtime_has:
            return getattr(runtime, attr_name), True
        return None, False

    tss_value, tss_has = _pick_bool_flag("tool_schema_shortlisting_enabled")
    bwa_value, bwa_has = _pick_bool_flag("allow_background_write_authorization")

    variant_value, variant_has = _merge_trailer_guidance_variant(
        agent_variant=profile.trailer_guidance_variant,
        runtime_variant=runtime.trailer_guidance_variant,
        agent_set=profile.has_trailer_guidance_variant,
        runtime_set=runtime.has_trailer_guidance_variant,
    )

    effective_thinking_policy = profile.thinking_policy
    effective_provider_policy = profile.provider_policy
    effective_plugins = profile.plugins

    effective_modes = dict(profile.modes or {})
    effective_tools = profile.tools

    return replace(
        profile,
        tool_schema_shortlisting_enabled=tss_value,
        has_tool_schema_shortlisting_enabled=tss_has,
        allow_background_write_authorization=bwa_value,
        has_allow_background_write_authorization=bwa_has,
        trailer_guidance_variant=(dict(variant_value or {}) if variant_has else None),
        has_trailer_guidance_variant=variant_has,
        thinking_policy=effective_thinking_policy,
        provider_policy=effective_provider_policy,
        plugins=effective_plugins,
        modes=effective_modes,
        tools=effective_tools,
    )


def resolve_agent_identity(
    config: OpenMinionConfig, agent_id: str | None = None
) -> AgentIdentityResolution:
    requested_agent_id = str(agent_id or "").strip()
    profile = resolve_agent_config(config, requested_agent_id or None)
    return AgentIdentityResolution(
        public_agent_id=requested_agent_id or profile.name,
        profile=profile,
    )


__all__ = [
    "ActionPolicyConfig",
    "ActionPolicyMatchConfig",
    "ActionPolicyRuleConfig",
    "AgentIdentityResolution",
    "AgentProfileConfig",
    "ChannelAuthenticityConfig",
    "ChannelPolicyConfig",
    "ConfigValidationError",
    "GatewayConfig",
    "OpenMinionConfig",
    "SecurityConfig",
    "StorageConfig",
    "VectorConfig",
    "configured_agent_ids",
    "resolve_agent_config",
    "resolve_agent_identity",
    "resolve_default_agent_id",
]
