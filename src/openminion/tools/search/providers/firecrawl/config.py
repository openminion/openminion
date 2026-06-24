from dataclasses import dataclass
from typing import Any

from openminion.tools.config import resolve_tool_context_env


FIRECRAWL_API_KEY_ENV = "FIRECRAWL_API_KEY"
FIRECRAWL_API_URL_ENV = "FIRECRAWL_API_URL"
FIRECRAWL_TIMEOUT_SECONDS_ENV = "FIRECRAWL_TIMEOUT_SECONDS"
DEFAULT_FIRECRAWL_API_URL = "https://api.firecrawl.dev"
DEFAULT_FIRECRAWL_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class FirecrawlSearchProviderConfig:
    endpoint: str = ""
    timeout_s: float = 0.0
    api_key: str | None = None


def load_config(*_args: object, **_kwargs: object) -> FirecrawlSearchProviderConfig:
    return FirecrawlSearchProviderConfig()


def resolve_firecrawl_api_key(*, ctx: Any | None = None) -> str:
    env = resolve_tool_context_env(ctx)
    return env.get(FIRECRAWL_API_KEY_ENV, "")


def resolve_firecrawl_api_url(*, ctx: Any | None = None) -> str:
    env = resolve_tool_context_env(ctx)
    return env.get(FIRECRAWL_API_URL_ENV, DEFAULT_FIRECRAWL_API_URL)


def resolve_firecrawl_timeout_seconds(*, ctx: Any | None = None) -> float:
    env = resolve_tool_context_env(ctx)
    raw = env.get(FIRECRAWL_TIMEOUT_SECONDS_ENV, "")
    try:
        value = float(raw) if raw else float(DEFAULT_FIRECRAWL_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        value = float(DEFAULT_FIRECRAWL_TIMEOUT_SECONDS)
    return max(0.0, value)


__all__ = [
    "DEFAULT_FIRECRAWL_API_URL",
    "DEFAULT_FIRECRAWL_TIMEOUT_SECONDS",
    "FirecrawlSearchProviderConfig",
    "FIRECRAWL_API_KEY_ENV",
    "FIRECRAWL_API_URL_ENV",
    "FIRECRAWL_TIMEOUT_SECONDS_ENV",
    "load_config",
    "resolve_firecrawl_api_key",
    "resolve_firecrawl_api_url",
    "resolve_firecrawl_timeout_seconds",
]
