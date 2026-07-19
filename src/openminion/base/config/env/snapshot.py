"""Snapshot-backed environment accessors for runtime config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
import os

from openminion.base.constants import (
    BASE_BOOL_FALSE_VALUES,
    BASE_BOOL_TRUE_VALUES,
    OPENMINION_CONFIG_PATH_ENV,
    OPENMINION_CONFIG_ROOT_ENV,
    OPENMINION_DATA_ROOT_ENFORCEMENT_ENV,
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_DISABLE_LLMCTL_BRIDGE_ENV,
    OPENMINION_GENERATED_ROOT_ENV,
    OPENMINION_HOME_ENV,
    OPENMINION_LLM_DEBUG_DIR_ENV,
    OPENMINION_LLM_DEBUG_ENV,
    OPENMINION_LLM_DEBUG_MAX_CHARS_ENV,
    OPENMINION_LLM_DEBUG_PROVIDER_ENV,
    OPENMINION_LOG_LEVEL_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
    OPENMINION_PROVIDER_INTERFACE_STRICT_ENV,
    OPENMINION_SHOW_RESPONSE_TIME_ENV,
    OPENMINION_STRICT_PROVIDER_RESPONSE_CONTRACTS_ENV,
    OPENMINION_TRACE_REQUESTS_DIR_ENV,
    OPENMINION_TRACE_REQUESTS_ENV,
    OPENMINION_TURN_TIMEOUT_SECONDS_ENV,
)


def _normalize_env_map(raw: Mapping[str, object] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if raw is None:
        return normalized
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        normalized[name] = str(value or "").strip()
    return normalized


def _parse_bool(raw: str | None, default: bool) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    if value in BASE_BOOL_TRUE_VALUES:
        return True
    if value in BASE_BOOL_FALSE_VALUES:
        return False
    return default


def _merged_env_values(
    runtime_env: Mapping[str, object] | None,
    process_env: Mapping[str, object] | None,
) -> dict[str, str]:
    merged = _normalize_env_map(runtime_env)
    merged.update(
        _normalize_env_map(process_env if process_env is not None else os.environ)
    )
    return merged


@dataclass(frozen=True)
class EnvironmentConfig:
    """Immutable, snapshot-backed environment accessor with typed helpers.

    Process environment values override runtime config env values so explicit
    shell exports win over config defaults.
    """

    values: Mapping[str, str]

    @classmethod
    def from_sources(
        cls,
        *,
        process_env: Mapping[str, object] | None = None,
        runtime_env: Mapping[str, object] | None = None,
    ) -> "EnvironmentConfig":
        return cls(values=_merged_env_values(runtime_env, process_env))

    def has(self, name: str) -> bool:
        return bool(str(self.get(name, "")).strip())

    def get(self, name: str, default: str = "") -> str:
        key = str(name or "").strip()
        if not key:
            return str(default or "")
        return str(self.values.get(key, default) or "")

    def get_bool(self, name: str, default: bool = False) -> bool:
        return _parse_bool(self.get(name, ""), default)

    def get_int(self, name: str, default: int = 0) -> int:
        raw = self.get(name, "")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(default)

    def get_float(self, name: str, default: float = 0.0) -> float:
        raw = self.get(name, "")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    def get_list(
        self,
        name: str,
        *,
        separator: str = ",",
        default: Sequence[str] | None = None,
    ) -> list[str]:
        raw = self.get(name, "")
        if not raw:
            return [str(item).strip() for item in (default or []) if str(item).strip()]
        parts = [item.strip() for item in raw.split(separator)]
        return [item for item in parts if item]

    def snapshot(self) -> dict[str, str]:
        return dict(self.values)

    @property
    def openminion_home(self) -> str:
        return self.get(OPENMINION_HOME_ENV, "")

    @property
    def openminion_data_root(self) -> str:
        return self.get(OPENMINION_DATA_ROOT_ENV, "")

    @property
    def openminion_generated_root(self) -> str:
        return self.get(OPENMINION_GENERATED_ROOT_ENV, "")

    @property
    def openminion_config_root(self) -> str:
        return self.get(OPENMINION_CONFIG_ROOT_ENV, "")

    @property
    def openminion_config_path(self) -> str:
        return self.get(OPENMINION_CONFIG_PATH_ENV, "")

    @property
    def openminion_data_root_enforcement(self) -> str:
        raw = self.get(OPENMINION_DATA_ROOT_ENFORCEMENT_ENV, "hard").strip().lower()
        if raw in {"soft", "warn"}:
            return "soft"
        return "hard"

    @property
    def openminion_module_standalone(self) -> bool:
        return self.get_bool(OPENMINION_MODULE_STANDALONE_ENV, False)

    @property
    def openminion_log_level(self) -> str:
        return self.get(OPENMINION_LOG_LEVEL_ENV, "")

    @property
    def openminion_trace_requests(self) -> bool:
        return self.get_bool(OPENMINION_TRACE_REQUESTS_ENV, False)

    @property
    def openminion_trace_requests_dir(self) -> str:
        return self.get(OPENMINION_TRACE_REQUESTS_DIR_ENV, "")

    @property
    def openminion_llm_debug(self) -> bool:
        return self.get_bool(OPENMINION_LLM_DEBUG_ENV, False)

    @property
    def openminion_llm_debug_provider(self) -> str:
        return self.get(OPENMINION_LLM_DEBUG_PROVIDER_ENV, "").strip().lower()

    @property
    def openminion_llm_debug_dir(self) -> str:
        return self.get(OPENMINION_LLM_DEBUG_DIR_ENV, "")

    @property
    def openminion_llm_debug_max_chars(self) -> int:
        return max(0, self.get_int(OPENMINION_LLM_DEBUG_MAX_CHARS_ENV, 0))

    @property
    def openminion_strict_provider_response_contracts(self) -> bool:
        return self.get_bool(OPENMINION_STRICT_PROVIDER_RESPONSE_CONTRACTS_ENV, False)

    @property
    def openminion_provider_interface_strict(self) -> bool:
        return self.get_bool(OPENMINION_PROVIDER_INTERFACE_STRICT_ENV, False)

    @property
    def openminion_disable_llmctl_bridge(self) -> bool:
        return self.get_bool(OPENMINION_DISABLE_LLMCTL_BRIDGE_ENV, False)

    @property
    def openminion_turn_timeout_seconds(self) -> int:
        return max(0, self.get_int(OPENMINION_TURN_TIMEOUT_SECONDS_ENV, 0))

    @property
    def openminion_show_response_time(self) -> bool:
        return self.get_bool(OPENMINION_SHOW_RESPONSE_TIME_ENV, True)

    @property
    def openai_api_key(self) -> str:
        return self.get("OPENAI_API_KEY", "")

    @property
    def anthropic_api_key(self) -> str:
        return self.get("ANTHROPIC_API_KEY", "")

    @property
    def openrouter_api_key(self) -> str:
        return self.get("OPENROUTER_API_KEY", "")

    @property
    def cerebras_api_key(self) -> str:
        return self.get("CEREBRAS_API_KEY", "")

    @property
    def groq_api_key(self) -> str:
        return self.get("GROQ_API_KEY", "")

    @property
    def ollama_api_key(self) -> str:
        return self.get("OLLAMA_API_KEY", "")

    @property
    def cortensor_api_key(self) -> str:
        return self.get("CORTENSOR_API_KEY", "")

    @property
    def tavily_api_key(self) -> str:
        return self.get("TAVILY_API_KEY", "")

    @property
    def brave_api_key(self) -> str:
        return self.get("BRAVE_API_KEY", "")

    @property
    def serpapi_api_key(self) -> str:
        return self.get("SERPAPI_API_KEY", "")

    @property
    def serper_api_key(self) -> str:
        return self.get("SERPER_API_KEY", "")

    @property
    def firecrawl_api_key(self) -> str:
        return self.get("FIRECRAWL_API_KEY", "")

    @property
    def tinyfish_api_key(self) -> str:
        return self.get("TINYFISH_API_KEY", "")

    @property
    def weatherapi_api_key(self) -> str:
        return self.get("WEATHERAPI_API_KEY", "")


def resolve_environment_config(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
) -> EnvironmentConfig:
    if isinstance(env, EnvironmentConfig):
        return env
    return EnvironmentConfig(
        values=_merged_env_values(
            env if isinstance(env, Mapping) else runtime_env, process_env
        )
    )


def resolve_environment_config_with_explicit_env(
    env: EnvironmentConfig | Mapping[str, object] | None,
) -> EnvironmentConfig:
    if isinstance(env, EnvironmentConfig):
        return env
    if env is None:
        return resolve_environment_config()
    return EnvironmentConfig(
        values=_merged_env_values(resolve_environment_config().snapshot(), env)
    )


__all__ = [
    "EnvironmentConfig",
    "resolve_environment_config",
    "resolve_environment_config_with_explicit_env",
]
