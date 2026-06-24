from collections.abc import Mapping
from dataclasses import dataclass

from openminion.tools.config import ToolEnv, get_tool_env_float, resolve_tool_env

from .constants import (
    DEFAULT_SERPAPI_API_URL,
    DEFAULT_SERPAPI_TIMEOUT_SECONDS,
    SERPAPI_API_URL_ENV,
    SERPAPI_TIMEOUT_SECONDS_ENV,
)


@dataclass(frozen=True)
class SerpApiSearchProviderConfig:
    endpoint: str = ""
    timeout_s: float = 0.0
    api_key: str | None = None


def get_serpapi_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return resolve_tool_env(env=env).serpapi_api_key.strip()


def get_serpapi_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return str(
        resolve_tool_env(env=env).get(SERPAPI_API_URL_ENV, DEFAULT_SERPAPI_API_URL)
        or ""
    ).strip()


def get_serpapi_timeout_seconds(
    default: float = DEFAULT_SERPAPI_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        SERPAPI_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def load_config(*_args: object, **_kwargs: object) -> SerpApiSearchProviderConfig:
    return SerpApiSearchProviderConfig()


__all__ = [
    "SerpApiSearchProviderConfig",
    "DEFAULT_SERPAPI_API_URL",
    "DEFAULT_SERPAPI_TIMEOUT_SECONDS",
    "get_serpapi_api_key",
    "get_serpapi_api_url",
    "get_serpapi_timeout_seconds",
    "load_config",
]
