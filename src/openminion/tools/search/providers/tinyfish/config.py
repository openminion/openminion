from collections.abc import Mapping
from dataclasses import dataclass

from openminion.tools.config import ToolEnv, get_tool_env_float, resolve_tool_env

from .constants import (
    DEFAULT_TINYFISH_SEARCH_API_URL,
    DEFAULT_TINYFISH_SEARCH_TIMEOUT_SECONDS,
    TINYFISH_SEARCH_API_URL_ENV,
    TINYFISH_SEARCH_TIMEOUT_SECONDS_ENV,
)


@dataclass(frozen=True)
class TinyFishSearchProviderConfig:
    endpoint: str = ""
    timeout_s: float = 0.0
    api_key: str | None = None


def resolve_tinyfish_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return resolve_tool_env(env=env).tinyfish_api_key.strip()


def resolve_tinyfish_search_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        str(
            resolve_tool_env(env=env).get(
                TINYFISH_SEARCH_API_URL_ENV,
                DEFAULT_TINYFISH_SEARCH_API_URL,
            )
            or ""
        ).strip()
        or DEFAULT_TINYFISH_SEARCH_API_URL
    )


def resolve_tinyfish_search_timeout_seconds(
    default: float = DEFAULT_TINYFISH_SEARCH_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        TINYFISH_SEARCH_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def load_config(*_args: object, **_kwargs: object) -> TinyFishSearchProviderConfig:
    return TinyFishSearchProviderConfig()


__all__ = [
    "DEFAULT_TINYFISH_SEARCH_API_URL",
    "DEFAULT_TINYFISH_SEARCH_TIMEOUT_SECONDS",
    "TinyFishSearchProviderConfig",
    "load_config",
    "resolve_tinyfish_api_key",
    "resolve_tinyfish_search_api_url",
    "resolve_tinyfish_search_timeout_seconds",
]
