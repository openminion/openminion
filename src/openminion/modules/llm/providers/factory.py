import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config
from openminion.modules.llm.config import resolve_provider_identity_translation
from openminion.modules.llm.constants import (
    LLM_TOOL_CALL_STRATEGY_HYBRID,
    LLM_TOOL_CHOICE_AUTO,
)
from openminion.modules.llm.providers.base import LLMProvider, ProviderError
from openminion.modules.llm.providers.bridge import (
    LLMCTLBridgeProvider,
    llmctl_bridge_available,
)


# Supported provider names (for health-check / doctor "is this provider known?" checks).

SUPPORTED_PROVIDERS = frozenset(
    {
        "echo",
        "openai",
        "anthropic",
        "claude",
        "openrouter",
        "cerebras",
        "groq",
        "ollama",
        "cortensor",
    }
)


@dataclass(frozen=True)
class RuntimeLLMHandle:
    name: str
    model: str
    client: Any
    tool_call_strategy: str = LLM_TOOL_CALL_STRATEGY_HYBRID


def build_runtime_llm_handle(
    config: OpenMinionConfig,
    logger: logging.Logger,
    *,
    provider_runtime_mode: str | None = None,
    registry: object | None = None,
) -> RuntimeLLMHandle:
    """Build a direct llm-module client handle for live runtime execution.

    This avoids bridge-class dependency on the hot path while preserving
    provider/model/tool-policy behavior used by `LLMCTLBridgeProvider`.
    """
    del provider_runtime_mode, registry
    try:
        _default_agent_id = resolve_default_agent_id(config)
        provider_name = (
            config.agents[_default_agent_id].provider or "echo"
        ).strip().lower() or "echo"
    except Exception:  # noqa: BLE001
        provider_name = "echo"

    provider_env = _resolve_provider_env(config)
    if not llmctl_bridge_available(env=provider_env):
        raise ProviderError(
            "openminion.modules.llm bridge is required for inference but is not available. "
            "Ensure openminion is installed or importable."
        )

    try:
        from openminion.modules.llm import LLMCTL
        from openminion.modules.llm.config import AgentProfile, ToolPolicy
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(
            f"Failed to import llm module runtime components: {exc}"
        ) from exc

    bridge_provider_name, model, provider_payload = (
        _provider_settings_for_llmctl_bridge(
            config,
            provider_name,
            env=provider_env,
        )
    )
    provider_payload["__env__"] = resolve_environment_config(
        env=provider_env
    ).snapshot()

    llmctl_config = {
        "version": 1,
        "llmctl": {
            "default_provider": bridge_provider_name,
            "default_model": model,
            "timeouts": {
                "request_timeout_sec": _as_positive_int(
                    provider_payload.get("timeout_seconds"), default=60
                ),
                "connect_timeout_sec": 10,
            },
            "retries": {
                "max_retries": 0 if bridge_provider_name == "cortensor" else 2,
                "backoff_ms": 300,
            },
            "logging": {"redaction": "normal", "include_provider_raw": False},
        },
        "providers": {
            bridge_provider_name: dict(provider_payload),
        },
        "agents": {
            "openminion_runtime": {
                "default_provider": bridge_provider_name,
                "default_model": model,
                "tool_policy": {
                    "enable_tools": True,
                    "allowed_tools": None,
                    "tool_choice_default": LLM_TOOL_CHOICE_AUTO,
                    "block_on_disallowed_tool_call": False,
                },
            }
        },
    }

    runtime = LLMCTL.from_config(llmctl_config)
    profile = AgentProfile(
        name="openminion_runtime",
        default_provider=bridge_provider_name,
        default_model=model,
        tool_policy=ToolPolicy(
            enable_tools=True,
            allowed_tools=None,
            tool_choice_default=LLM_TOOL_CHOICE_AUTO,
            block_on_disallowed_tool_call=False,
        ),
    )
    client = runtime.client(profile=profile)
    tool_call_strategy = _resolve_provider_tool_call_strategy(config, provider_name)
    logger.debug("using provider=%s runtime=llmctl_client", bridge_provider_name)
    return RuntimeLLMHandle(
        name=bridge_provider_name,
        model=model,
        client=client,
        tool_call_strategy=tool_call_strategy,
    )


def build_provider(
    config: OpenMinionConfig,
    logger: logging.Logger,
    *,
    prefer_llmctl_bridge: bool = True,
    provider_runtime_mode: str | None = None,
    registry: object | None = None,
) -> LLMProvider:
    """Build provider helper."""
    del registry  # no longer used
    try:
        _default_agent_id = resolve_default_agent_id(config)
        provider_name = (
            config.agents[_default_agent_id].provider or "echo"
        ).strip().lower() or "echo"
    except Exception:  # noqa: BLE001
        provider_name = "echo"

    provider_env = _resolve_provider_env(config)
    if not llmctl_bridge_available(env=provider_env):
        raise ProviderError(
            "openminion.modules.llm bridge is required for inference but is not available. "
            "Ensure openminion is installed or importable."
        )

    provider = _build_llmctl_bridge_provider(
        config,
        logger,
        provider_name,
        env=provider_env,
    )
    logger.debug("using provider=%s runtime=llmctl_bridge", provider_name)
    return provider


def _build_llmctl_bridge_provider(
    config: OpenMinionConfig,
    logger: logging.Logger,
    provider_name: str,
    *,
    env: Mapping[str, str],
) -> LLMProvider:
    del logger
    bridge_provider_name, model, provider_payload = (
        _provider_settings_for_llmctl_bridge(
            config,
            provider_name,
            env=env,
        )
    )
    provider_payload["__env__"] = resolve_environment_config(env=env).snapshot()
    return LLMCTLBridgeProvider(
        provider_name=bridge_provider_name,
        model=model,
        provider_config=provider_payload,
        env=env,
    )


def _provider_settings_for_llmctl_bridge(
    config: OpenMinionConfig,
    provider_name: str,
    *,
    env: Mapping[str, str],
) -> tuple[str, str, dict]:
    key = provider_name.strip().lower() or "echo"
    if key == "echo":
        return ("echo", "echo", {"model": "echo"})

    if key == "openai":
        payload = asdict(config.providers.openai)
        if not payload.get("provider_identity"):
            translated_identity = resolve_provider_identity_translation(
                "openai",
                model=str(payload.get("model") or "").strip(),
                base_url=str(payload.get("base_url") or "").strip(),
            )
            if translated_identity:
                payload["provider_identity"] = translated_identity
        payload["api_key"] = _resolve_api_key(
            config_key=config.providers.openai.api_key,
            env_name=config.providers.openai.api_key_env,
            default_env="OPENAI_API_KEY",
            env=env,
        )
        if not str(payload["api_key"]).strip():
            env_name = config.providers.openai.api_key_env.strip() or "OPENAI_API_KEY"
            raise ProviderError(
                "OpenAI provider selected but API key is missing. "
                f"Set providers.openai.api_key or export {env_name}."
            )
        return ("openai", str(payload.get("model", "gpt-4.1-mini")), payload)

    if key in {"anthropic", "claude"}:
        payload = asdict(config.providers.anthropic)
        payload["api_key"] = _resolve_api_key(
            config_key=config.providers.anthropic.api_key,
            env_name=config.providers.anthropic.api_key_env,
            default_env="ANTHROPIC_API_KEY",
            env=env,
        )
        if not str(payload["api_key"]).strip():
            env_name = (
                config.providers.anthropic.api_key_env.strip() or "ANTHROPIC_API_KEY"
            )
            raise ProviderError(
                "Anthropic provider selected but API key is missing. "
                f"Set providers.anthropic.api_key or export {env_name}."
            )
        return (key, str(payload.get("model", "claude-3-5-sonnet-latest")), payload)

    if key == "openrouter":
        payload = asdict(config.providers.openrouter)
        payload["api_key"] = _resolve_api_key(
            config_key=config.providers.openrouter.api_key,
            env_name=config.providers.openrouter.api_key_env,
            default_env="OPENROUTER_API_KEY",
            env=env,
        )
        if not str(payload["api_key"]).strip():
            env_name = (
                config.providers.openrouter.api_key_env.strip() or "OPENROUTER_API_KEY"
            )
            raise ProviderError(
                "OpenRouter provider selected but API key is missing. "
                f"Set providers.openrouter.api_key or export {env_name}."
            )
        return ("openrouter", str(payload.get("model", "openai/gpt-4.1-mini")), payload)

    if key == "cerebras":
        payload = asdict(config.providers.cerebras)
        payload["api_key"] = _resolve_api_key(
            config_key=config.providers.cerebras.api_key,
            env_name=config.providers.cerebras.api_key_env,
            default_env="CEREBRAS_API_KEY",
            env=env,
        )
        if not str(payload["api_key"]).strip():
            env_name = (
                config.providers.cerebras.api_key_env.strip() or "CEREBRAS_API_KEY"
            )
            raise ProviderError(
                "Cerebras provider selected but API key is missing. "
                f"Set providers.cerebras.api_key or export {env_name}."
            )
        return ("cerebras", str(payload.get("model", "gpt-oss-120b")), payload)

    if key == "groq":
        payload = asdict(config.providers.groq)
        payload["api_key"] = _resolve_api_key(
            config_key=config.providers.groq.api_key,
            env_name=config.providers.groq.api_key_env,
            default_env="GROQ_API_KEY",
            env=env,
        )
        if not str(payload["api_key"]).strip():
            env_name = config.providers.groq.api_key_env.strip() or "GROQ_API_KEY"
            raise ProviderError(
                "Groq provider selected but API key is missing. "
                f"Set providers.groq.api_key or export {env_name}."
            )
        return ("groq", str(payload.get("model", "llama-3.3-70b-versatile")), payload)

    if key == "ollama":
        payload = asdict(config.providers.ollama)
        payload["api_key"] = _resolve_api_key(
            config_key=config.providers.ollama.api_key,
            env_name=config.providers.ollama.api_key_env,
            default_env="OLLAMA_API_KEY",
            env=env,
        )
        return ("ollama", str(payload.get("model", "llama3.1")), payload)

    if key == "cortensor":
        runtime_config = _resolve_cortensor_runtime_config(config, env=env)
        payload = asdict(runtime_config)
        payload["api_key"] = _resolve_api_key(
            config_key=runtime_config.api_key,
            env_name=runtime_config.api_key_env,
            default_env="CORTENSOR_API_KEY",
            env=env,
        )
        return ("cortensor", str(payload.get("model", "gpt-oss-20b")), payload)

    raise ProviderError(
        f"Unknown provider '{key}'. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
    )


def _resolve_api_key(
    config_key: str,
    env_name: str,
    default_env: str,
    *,
    env: Mapping[str, str],
) -> str:
    configured = str(config_key or "").strip()
    if configured:
        return configured
    env_key = env_name.strip() or default_env
    env_value = str(env.get(env_key, "") or "").strip()
    if env_value:
        return env_value
    return ""


def _resolve_cortensor_runtime_config(
    config: OpenMinionConfig, *, env: Mapping[str, str]
):
    from openminion.modules.llm.providers.cortensor.config import (
        resolve_cortensor_runtime_config as _resolve,
    )

    return _resolve(config, env=env)


def _resolve_provider_env(config: OpenMinionConfig) -> Mapping[str, str]:
    runtime_env = getattr(getattr(config, "runtime", None), "env", {}) or {}
    if not isinstance(runtime_env, Mapping):
        runtime_env = {}
    return resolve_environment_config(runtime_env=runtime_env).snapshot()


def _resolve_provider_tool_call_strategy(
    config: OpenMinionConfig,
    provider_name: str,
) -> str:
    providers_cfg = getattr(config, "providers", None)
    provider_cfg = (
        getattr(providers_cfg, provider_name, None) if providers_cfg else None
    )
    configured = str(getattr(provider_cfg, "tool_call_strategy", "") or "").strip()
    return configured or LLM_TOOL_CALL_STRATEGY_HYBRID


def _as_positive_int(value, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    if parsed <= 0:
        return int(default)
    return parsed


def _as_non_negative_int(value, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    if parsed < 0:
        return int(default)
    return parsed
