from collections.abc import Mapping
from dataclasses import dataclass

from openminion.tools.config import ToolEnv, get_tool_env_float, resolve_tool_env

from .constants import (
    DEFAULT_FIRECRAWL_API_URL,
    DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
    FIRECRAWL_API_KEY_ENV,
    FIRECRAWL_API_URL_ENV,
    FIRECRAWL_TIMEOUT_SECONDS_ENV,
)


@dataclass(frozen=True)
class FirecrawlFetchProviderConfig:
    endpoint: str = ""
    timeout_s: float = 0.0
    api_key: str | None = None


def resolve_firecrawl_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return str(resolve_tool_env(env=env).get(FIRECRAWL_API_KEY_ENV, "") or "").strip()


def resolve_firecrawl_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        str(
            resolve_tool_env(env=env).get(
                FIRECRAWL_API_URL_ENV, DEFAULT_FIRECRAWL_API_URL
            )
            or ""
        ).strip()
        or DEFAULT_FIRECRAWL_API_URL
    )


def resolve_firecrawl_timeout_seconds(
    default: float = DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        FIRECRAWL_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def load_config(*_args: object, **_kwargs: object) -> FirecrawlFetchProviderConfig:
    return FirecrawlFetchProviderConfig()


__all__ = [
    "DEFAULT_FIRECRAWL_API_URL",
    "DEFAULT_FIRECRAWL_TIMEOUT_SECONDS",
    "FirecrawlFetchProviderConfig",
    "load_config",
    "resolve_firecrawl_api_key",
    "resolve_firecrawl_api_url",
    "resolve_firecrawl_timeout_seconds",
]
