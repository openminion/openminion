import dataclasses
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from openminion.base.config import OpenMinionConfig
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config

from .constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_INTEGRATED_CONFIG_SUBPATH,
    LLM_TOOL_CHOICE_AUTO,
)
from .errors import ErrorCode, LLMCtlError
from .schemas import ToolChoice

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    yaml = None  # type: ignore[assignment]

SoftInputCapAction = Literal["allow", "error"]


class TimeoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_timeout_sec: int = 60
    connect_timeout_sec: int = 10


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: int = 2
    backoff_ms: int = 300


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redaction: Literal["off", "normal", "strict"] = "normal"
    include_provider_raw: bool = False


class BudgetDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soft_input_cap_action: SoftInputCapAction = "allow"


class RoutingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str


class RoutingFallback(RoutingTarget):
    model_config = ConfigDict(extra="forbid")

    on: List[ErrorCode] = Field(default_factory=list)


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: Optional[RoutingTarget] = None
    fallbacks: List[RoutingFallback] = Field(default_factory=list)


class GenerationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    stop: Optional[List[str]] = None


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    soft_input_cap_action: Optional[SoftInputCapAction] = None


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_tools: bool = False
    allowed_tools: Optional[List[str]] = None
    tool_choice_default: Optional[ToolChoice] = LLM_TOOL_CHOICE_AUTO
    block_on_disallowed_tool_call: bool = True


class RetryOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: Optional[int] = None
    backoff_ms: Optional[int] = None


class ProfileLogging(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_provider_raw: Optional[bool] = None


class ProviderIdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport_adapter: Optional[str] = None
    wire_protocol_family: Optional[str] = None
    service_vendor: Optional[str] = None
    model_family: Optional[str] = None
    upstream_vendor_hint: Optional[str] = None


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    generation_defaults: GenerationDefaults = Field(default_factory=GenerationDefaults)
    budgets: BudgetPolicy = Field(default_factory=BudgetPolicy)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    routing: Optional[RoutingPolicy] = None
    retries: RetryOverrides = Field(
        default_factory=RetryOverrides,
        validation_alias=AliasChoices("retries", "retry_overrides"),
    )
    logging: ProfileLogging = Field(default_factory=ProfileLogging)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    api_key_env: Optional[str] = None
    base_url: Optional[str] = None
    org: Optional[str] = None
    project: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    provider_identity: Optional[ProviderIdentityConfig] = None
    cost_hint: Optional["ProviderCostHint"] = None


class ProviderCostHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_1k: Optional[float] = None
    output_per_1k: Optional[float] = None


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    generation_defaults: GenerationDefaults = Field(default_factory=GenerationDefaults)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retries: RetryConfig = Field(default_factory=RetryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    budgets: BudgetDefaults = Field(default_factory=BudgetDefaults)
    routing_defaults: Optional[RoutingPolicy] = None


class LLMCTLConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    llmctl: GlobalConfig = Field(default_factory=GlobalConfig)
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)
    agents: Dict[str, AgentProfile] = Field(default_factory=dict)


def resolve_provider_identity_translation(
    provider_name: str,
    *,
    model: str = "",
    base_url: str = "",
) -> Dict[str, str]:
    normalized_provider = str(provider_name or "").strip().lower()
    if normalized_provider != "openai":
        return {}

    return {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": _resolve_openai_service_vendor(base_url),
        "model_family": _resolve_model_family(model),
    }


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> LLMCTLConfig:
    runtime_env = getattr(getattr(base_config, "runtime", None), "env", {}) or {}
    if not isinstance(runtime_env, Mapping):
        runtime_env = {}
    env_config = resolve_environment_config(runtime_env=runtime_env)
    candidates = (
        (home_root / DEFAULT_CONFIG_FILENAME).resolve(strict=False),
        (data_root / DEFAULT_INTEGRATED_CONFIG_SUBPATH).resolve(strict=False),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(candidate)
    providers: Dict[str, ProviderConfig] = {}
    provider_names = (
        "openai",
        "anthropic",
        "openrouter",
        "cerebras",
        "groq",
        "ollama",
        "cortensor",
    )
    for name in provider_names:
        provider_cfg = getattr(base_config.providers, name, None)
        if provider_cfg is None:
            continue
        payload = (
            dataclasses.asdict(provider_cfg)
            if dataclasses.is_dataclass(provider_cfg)
            else {}
        )
        api_key_env = str(payload.get("api_key_env") or "").strip()
        if api_key_env and not str(payload.get("api_key") or "").strip():
            env_value = str(env_config.get(api_key_env, "") or "").strip()
            if env_value:
                payload["api_key"] = env_value
        providers[name] = ProviderConfig.model_validate(payload)

    try:
        _default_agent_id = resolve_default_agent_id(base_config)
        default_provider = (
            str(base_config.agents[_default_agent_id].provider or "").strip().lower()
            or None
        )
    except Exception:  # noqa: BLE001
        default_provider = None
    default_model: Optional[str] = None
    default_temperature: Optional[float] = None
    default_timeout: Optional[int] = None
    if default_provider and default_provider in provider_names:
        provider_cfg = getattr(base_config.providers, default_provider, None)
        if provider_cfg is not None:
            default_model = getattr(provider_cfg, "model", None) or None
            default_temperature = getattr(provider_cfg, "temperature", None)
            default_timeout = getattr(provider_cfg, "timeout_seconds", None)

    generation_defaults = (
        GenerationDefaults(temperature=float(default_temperature))
        if default_temperature is not None
        else GenerationDefaults()
    )
    timeouts = (
        TimeoutConfig(request_timeout_sec=int(default_timeout))
        if default_timeout is not None
        else TimeoutConfig()
    )

    return LLMCTLConfig(
        llmctl=GlobalConfig(
            default_provider=default_provider,
            default_model=default_model,
            generation_defaults=generation_defaults,
            timeouts=timeouts,
        ),
        providers=providers,
    )


def load_config(
    path_or_dict: Union[str, Path, Dict[str, Any], LLMCTLConfig],
) -> LLMCTLConfig:
    if isinstance(path_or_dict, LLMCTLConfig):
        config = path_or_dict
    elif isinstance(path_or_dict, dict):
        config = LLMCTLConfig.model_validate(path_or_dict)
    else:
        path = Path(path_or_dict).expanduser().resolve(strict=False)
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        if yaml is None:
            raise LLMCtlError(
                "INTERNAL_ERROR",
                "PyYAML is required to load config files from disk",
                {"path": str(path)},
            )
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                "llmctl config must parse to an object",
                {"path": str(path)},
            )
        config = LLMCTLConfig.model_validate(parsed)

    fixed_agents: Dict[str, AgentProfile] = {}
    for name, profile in config.agents.items():
        fixed_agents[name] = profile.model_copy(update={"name": profile.name or name})

    return config.model_copy(update={"agents": fixed_agents})


def resolve_provider_config(
    config: LLMCTLConfig,
    provider_name: str,
    *,
    env: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    provider_cfg = config.providers.get(provider_name)
    if provider_cfg is None:
        return {}
    data = provider_cfg.model_dump(exclude_none=True)
    provider_identity = data.get("provider_identity")
    if not isinstance(provider_identity, dict) or not provider_identity:
        translated = resolve_provider_identity_translation(
            provider_name,
            model=str(data.get("model") or "").strip(),
            base_url=str(data.get("base_url") or "").strip(),
        )
        if translated:
            data["provider_identity"] = translated
    api_key_env = data.get("api_key_env")
    if (
        isinstance(api_key_env, str)
        and api_key_env
        and not str(data.get("api_key") or "").strip()
    ):
        env_value = _read_env_value(api_key_env, env=env)
        if env_value:
            data["api_key"] = env_value
    return data


def _read_env_value(key: str, *, env: Mapping[str, Any] | None = None) -> str:
    if env is not None:
        value = env.get(key)
        return str(value or "").strip()
    resolved = resolve_environment_config()
    return str(resolved.get(key, "") or "").strip()


def _resolve_openai_service_vendor(base_url: str) -> str:
    endpoint = str(base_url or "").strip().lower()
    if "dashscope.aliyuncs.com" in endpoint:
        return "dashscope"
    if "api.minimax.io" in endpoint:
        return "minimax"
    return "openai"


def _resolve_model_family(model: str) -> str:
    lowered = str(model or "").strip().lower()
    if "minimax" in lowered:
        return "minimax"
    if "qwen" in lowered:
        return "qwen"
    if "glm" in lowered:
        return "glm"
    if "kimi" in lowered:
        return "kimi"
    if "claude" in lowered:
        return "claude"
    if lowered.startswith(("gpt", "o1", "o3", "o4")):
        return "gpt"
    return "openai"
