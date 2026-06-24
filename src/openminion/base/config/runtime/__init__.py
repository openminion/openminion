"""Runtime config dataclasses and identity path resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_HOME_ENV,
    OPENMINION_IDENTITY_DB_ENV,
    OPENMINION_IDENTITY_ROOT_ENV,
)
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.config.runtime.capability import (
    ModeRuntimePolicyConfig,
    PluginRuntimePolicyConfig,
    ProviderRuntimePolicyConfig,
    ThinkingRuntimePolicyConfig,
    coerce_mode_runtime_policy_map,
    coerce_plugin_runtime_policy_config,
    coerce_provider_runtime_policy_config,
    coerce_thinking_runtime_policy_config,
)
from openminion.base.config.mcp import (
    MCPExposureConfig,
    MCPPublishConfig,
    MCPServerConfig,
    MCPStdioSandboxConfig,
    MCPToolRiskOverrideConfig,
    coerce_mcp_publish_config,
    coerce_mcp_server_configs,
    normalize_mcp_sampling_mode,
)
from openminion.base.config.paths import resolve_data_root
from openminion.base.config.runtime.tools import (
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
)
from openminion.base.config.tool_selection import ToolSelectionConfig

_BASE_IDENTITY_DIRNAME = "identity"
_BASE_IDENTITY_DB_FILENAME = "identity.db"


@dataclass
class ToolPolicyConfig:
    default_required_scopes: list[str] = field(default_factory=lambda: ["tool.execute"])
    max_calls_per_run: int = 50
    max_calls_per_tool: int = 4
    max_budget_cost_per_run: int = 16


@dataclass
class OTELExporterConfig:
    enabled: bool = False
    endpoint: str = ""
    protocol: str = "http"
    service_name: str = "openminion"
    sample_rate: float = 1.0
    include_assistant_body: bool = False
    backend: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class RuntimeConfig:
    log_level: str = "INFO"
    demo_mode: bool = False
    env: dict[str, str] = field(default_factory=dict)
    process_mode: str = "daemon"
    daemon_auto_start: bool = True
    ipc_host: str = "127.0.0.1"
    ipc_port: int = 18789
    ipc_token: str = ""
    daemon_pid_file: str = ""
    daemon_log_file: str = ""
    session_keep_recent_messages: int = 20
    session_max_compact_per_turn: int = 100
    session_summary_max_chars: int = 8000
    session_archive_enabled: bool = True
    session_archive_root_path: str = ""
    session_archive_ref_limit: int = 3
    session_context_token_budget: int = 0
    session_context_chars_per_token: float = 4.0
    session_summary_enrichment_enabled: bool = False
    session_thread_ttl_seconds: int = 0
    session_writer_lease_seconds: int = 0
    agent_loop_max_steps: int = 50
    agent_loop_tool_result_max_chars: int = 4000
    brain_turn_timeout_seconds: int = 120
    provider_retry_max_attempts: int = 3
    chat_turn_timeout_seconds: float = 90.0
    chat_turn_max_attempts: int = 2
    memory_enabled: bool = True
    memory_root_path: str = ""
    tool_workspace_root: str = ""
    memory_retrieval_max_chars: int = 2000
    memory_log_retention_days: int = 30
    memory_max_facts: int = 200
    memory_max_todos: int = 200
    memory_patch_retention_count: int = 200
    memory_lock_ttl_seconds: int = 30
    memory_lock_acquire_timeout_seconds: int = 5
    memory_provider: str = "memory_v2"
    memory_capsule_strategy: str = "dynamic_turn"
    memory_dynamic_retrieval_enabled: bool = False
    telemetry_enabled: bool = False
    telemetry_db_path: str = ""
    telemetry_exporter: OTELExporterConfig = field(default_factory=OTELExporterConfig)
    debug_enabled: bool = True
    debug_cli_enabled: bool = True
    debug_api_enabled: bool = True
    debug_chat_enabled: bool = True
    debug_module_probes_enabled: bool = True
    menu_pairing_enabled: bool = True
    reactions_enabled: bool = True
    reactions_default_policy: str = "allow"
    clarify_llm_provider: str = ""
    clarify_llm_model: str = ""
    clarify_llm_temperature: float = 0.0
    clarify_llm_max_tokens: int = 256
    complex_request_plan_policy: str = "balanced"
    tool_selection: ToolSelectionConfig = field(default_factory=ToolSelectionConfig)
    tool_schema_shortlisting_enabled: bool | None = None
    has_tool_schema_shortlisting_enabled: bool = field(default=False, repr=False)
    allow_background_write_authorization: bool | None = None
    has_allow_background_write_authorization: bool = field(default=False, repr=False)
    trailer_guidance_variant: dict[str, str] | None = None
    has_trailer_guidance_variant: bool = field(default=False, repr=False)
    tools: ToolRuntimeConfig = field(default_factory=ToolRuntimeConfig)
    provider_policy: ProviderRuntimePolicyConfig | None = None
    thinking_policy: ThinkingRuntimePolicyConfig | None = None
    modes: dict[str, ModeRuntimePolicyConfig] = field(default_factory=dict)
    plugins: PluginRuntimePolicyConfig | None = None
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    mcp_publish: MCPPublishConfig = field(default_factory=MCPPublishConfig)
    mcp_sampling_mode: str = "disabled"
    mcp_discovery_cache_ttl_seconds: float = 0.0
    mcp_deferred_discovery_enabled: bool = False

    def __post_init__(self) -> None:
        self.tools = coerce_tool_runtime_config(self.tools)
        self.provider_policy = coerce_provider_runtime_policy_config(
            self.provider_policy,
            field_path="runtime.provider_policy",
        )
        self.thinking_policy = coerce_thinking_runtime_policy_config(
            self.thinking_policy,
            field_path="runtime.thinking_policy",
        )
        self.modes = coerce_mode_runtime_policy_map(
            self.modes,
            field_path="runtime.modes",
        )
        self.plugins = coerce_plugin_runtime_policy_config(
            self.plugins,
            field_path="runtime.plugins",
        )
        self.mcp_servers = coerce_mcp_server_configs(self.mcp_servers)
        self.mcp_publish = coerce_mcp_publish_config(self.mcp_publish)
        self.mcp_sampling_mode = normalize_mcp_sampling_mode(self.mcp_sampling_mode)
        try:
            self.mcp_discovery_cache_ttl_seconds = float(
                self.mcp_discovery_cache_ttl_seconds
            )
        except (TypeError, ValueError):
            self.mcp_discovery_cache_ttl_seconds = 0.0
        self.mcp_discovery_cache_ttl_seconds = max(
            0.0,
            self.mcp_discovery_cache_ttl_seconds,
        )
        self.mcp_deferred_discovery_enabled = bool(self.mcp_deferred_discovery_enabled)


@dataclass
class SelfImprovementConfig:
    enabled: bool = True
    notes_path: str = ""
    application_mode: str = "automatic"
    activation_threshold: int = 2
    max_applied_notes: int = 3
    min_token_overlap: int = 1
    auto_capture_tool_failures: bool = True


@dataclass
class IdentityConfig:
    db_path: str = ""
    bundle_root: str = ""
    root: str = ""


@dataclass
class IdentityBudgetCompactionConfig:
    enabled: bool = False
    provider: str = ""
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 120


@dataclass
class IdentityBudgetConfig:
    total_tokens: int = 200
    section_order: list[str] = field(
        default_factory=lambda: [
            "constraints",
            "tool_posture",
            "mission",
            "responsibilities",
            "voice",
            "notes",
        ]
    )
    section_priority: dict[str, int] = field(default_factory=dict)
    section_caps: dict[str, int] = field(default_factory=dict)
    truncate_strategy: str = "sentences"
    compaction: IdentityBudgetCompactionConfig = field(
        default_factory=IdentityBudgetCompactionConfig
    )


@dataclass
class ContextConfig:
    identity_budget: IdentityBudgetConfig | None = None


def _resolve_identity_home_root(
    env: EnvironmentConfig, *, home_root: Path | None = None
) -> Path:
    if home_root is not None:
        return home_root.expanduser().resolve()
    env_home = env.get(OPENMINION_HOME_ENV, "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    return Path.cwd().resolve()


def resolve_identity_root_from_env(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    home_root: Path | None = None,
) -> Path:
    resolved_env = resolve_environment_config(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    base_root = _resolve_identity_home_root(resolved_env, home_root=home_root)
    data_root = resolve_data_root(
        base_root,
        data_root=resolved_env.get(OPENMINION_DATA_ROOT_ENV, ""),
    )
    configured_root = resolved_env.get(OPENMINION_IDENTITY_ROOT_ENV, "").strip()
    if configured_root:
        candidate = Path(configured_root).expanduser()
        if not candidate.is_absolute():
            candidate = data_root / candidate
        return candidate.resolve()
    return (data_root / _BASE_IDENTITY_DIRNAME).resolve()


def resolve_identity_db_from_env(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
    home_root: Path | None = None,
) -> Path:
    resolved_env = resolve_environment_config(
        env=env,
        runtime_env=runtime_env,
        process_env=process_env,
    )
    configured_db = resolved_env.get(OPENMINION_IDENTITY_DB_ENV, "").strip()
    if configured_db:
        candidate = Path(configured_db).expanduser()
        if not candidate.is_absolute():
            candidate = (
                resolve_identity_root_from_env(
                    env=resolved_env,
                    home_root=home_root,
                )
                / candidate
            )
        return candidate.resolve()
    return (
        resolve_identity_root_from_env(env=resolved_env, home_root=home_root)
        / _BASE_IDENTITY_DB_FILENAME
    ).resolve()


__all__ = [
    "ContextConfig",
    "IdentityBudgetCompactionConfig",
    "IdentityBudgetConfig",
    "resolve_identity_db_from_env",
    "resolve_identity_root_from_env",
    "IdentityConfig",
    "MCPExposureConfig",
    "MCPServerConfig",
    "MCPStdioSandboxConfig",
    "MCPToolRiskOverrideConfig",
    "ModeRuntimePolicyConfig",
    "PluginRuntimePolicyConfig",
    "ProviderRuntimePolicyConfig",
    "ThinkingRuntimePolicyConfig",
    "RuntimeConfig",
    "SelfImprovementConfig",
    "ToolRuntimeConfig",
    "ToolPolicyConfig",
]
