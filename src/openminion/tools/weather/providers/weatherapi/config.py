from dataclasses import dataclass
from typing import Any

from openminion.tools.config import resolve_tool_context_env

WEATHERAPI_API_KEY_ENV = "WEATHERAPI_API_KEY"
WEATHERAPI_API_URL_ENV = "WEATHERAPI_API_URL"
WEATHERAPI_TIMEOUT_SECONDS_ENV = "WEATHERAPI_TIMEOUT_SECONDS"
DEFAULT_WEATHERAPI_API_URL = "https://api.weatherapi.com/v1"
DEFAULT_WEATHERAPI_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class WeatherApiProviderConfig:
    api_key: str = ""
    api_url: str = ""
    timeout_s: float = 0.0


def load_config(*_args: object, **_kwargs: object) -> WeatherApiProviderConfig:
    return WeatherApiProviderConfig()


def resolve_weatherapi_api_key(*, ctx: Any | None = None) -> str:
    env = resolve_tool_context_env(ctx)
    return env.get(WEATHERAPI_API_KEY_ENV, "")


def resolve_weatherapi_api_url(*, ctx: Any | None = None) -> str:
    env = resolve_tool_context_env(ctx)
    return env.get(WEATHERAPI_API_URL_ENV, DEFAULT_WEATHERAPI_API_URL)


def resolve_weatherapi_timeout_seconds(*, ctx: Any | None = None) -> float:
    env = resolve_tool_context_env(ctx)
    raw = env.get(WEATHERAPI_TIMEOUT_SECONDS_ENV, "")
    try:
        value = float(raw) if raw else float(DEFAULT_WEATHERAPI_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        value = float(DEFAULT_WEATHERAPI_TIMEOUT_SECONDS)
    return max(0.0, value)


__all__ = [
    "DEFAULT_WEATHERAPI_API_URL",
    "DEFAULT_WEATHERAPI_TIMEOUT_SECONDS",
    "WeatherApiProviderConfig",
    "WEATHERAPI_API_KEY_ENV",
    "WEATHERAPI_API_URL_ENV",
    "WEATHERAPI_TIMEOUT_SECONDS_ENV",
    "load_config",
    "resolve_weatherapi_api_key",
    "resolve_weatherapi_api_url",
    "resolve_weatherapi_timeout_seconds",
]
