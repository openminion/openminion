from collections.abc import Mapping
from dataclasses import dataclass

from openminion.tools.config import ToolEnv, get_tool_env_float, resolve_tool_env

from .constants import (
    DEFAULT_SERPER_API_URL,
    DEFAULT_SERPER_TIMEOUT_SECONDS,
    SERPER_API_URL_ENV,
    SERPER_TIMEOUT_SECONDS_ENV,
)


@dataclass(frozen=True)
class SerperSearchProviderConfig:
    endpoint: str = ""
    timeout_s: float = 0.0
    api_key: str | None = None


def get_serper_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return resolve_tool_env(env=env).serper_api_key.strip()


def get_serper_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return str(
        resolve_tool_env(env=env).get(SERPER_API_URL_ENV, DEFAULT_SERPER_API_URL) or ""
    ).strip()


def get_serper_timeout_seconds(
    default: float = DEFAULT_SERPER_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        SERPER_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def load_config(*_args: object, **_kwargs: object) -> SerperSearchProviderConfig:
    return SerperSearchProviderConfig()


__all__ = [
    "SerperSearchProviderConfig",
    "DEFAULT_SERPER_API_URL",
    "DEFAULT_SERPER_TIMEOUT_SECONDS",
    "get_serper_api_key",
    "get_serper_api_url",
    "get_serper_timeout_seconds",
    "load_config",
]
