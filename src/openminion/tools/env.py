from collections.abc import Mapping

from .config import (
    ToolEnv,
    get_tool_env,
    get_tool_env_float,
    get_tool_env_list,
    resolve_tool_env,
)
from .constants import OPENMINION_WEB_SEARCH_PROVIDER_ENV
from .ip.config import DEFAULT_IP_PUBLIC_TIMEOUT_SECONDS
from .ip.constants import (
    DEFAULT_IP_PUBLIC_LOOKUP_ENDPOINTS,
    OPENMINION_IP_PUBLIC_ENDPOINTS_ENV,
    OPENMINION_IP_PUBLIC_TIMEOUT_SECONDS_ENV,
)

SERPAPI_API_URL_ENV = "SERPAPI_API_URL"
SERPAPI_TIMEOUT_SECONDS_ENV = "SERPAPI_TIMEOUT_SECONDS"
DEFAULT_SERPAPI_API_URL = "https://serpapi.com/search"
DEFAULT_SERPAPI_TIMEOUT_SECONDS = 20.0
TAVILY_API_URL_ENV = "TAVILY_API_URL"
TAVILY_TIMEOUT_SECONDS_ENV = "TAVILY_TIMEOUT_SECONDS"
DEFAULT_TAVILY_TIMEOUT_SECONDS = 12.0
SERPER_API_URL_ENV = "SERPER_API_URL"
SERPER_TIMEOUT_SECONDS_ENV = "SERPER_TIMEOUT_SECONDS"
DEFAULT_SERPER_API_URL = "https://google.serper.dev/search"
DEFAULT_SERPER_TIMEOUT_SECONDS = 20.0
FIRECRAWL_API_URL_ENV = "FIRECRAWL_API_URL"
FIRECRAWL_TIMEOUT_SECONDS_ENV = "FIRECRAWL_TIMEOUT_SECONDS"
DEFAULT_FIRECRAWL_API_URL = "https://api.firecrawl.dev"
DEFAULT_FIRECRAWL_TIMEOUT_SECONDS = 20.0
TINYFISH_SEARCH_API_URL_ENV = "TINYFISH_SEARCH_API_URL"
TINYFISH_SEARCH_TIMEOUT_SECONDS_ENV = "TINYFISH_SEARCH_TIMEOUT_SECONDS"
DEFAULT_TINYFISH_SEARCH_API_URL = "https://api.search.tinyfish.ai"
DEFAULT_TINYFISH_SEARCH_TIMEOUT_SECONDS = 20.0
TINYFISH_FETCH_API_URL_ENV = "TINYFISH_FETCH_API_URL"
TINYFISH_FETCH_TIMEOUT_SECONDS_ENV = "TINYFISH_FETCH_TIMEOUT_SECONDS"
DEFAULT_TINYFISH_FETCH_API_URL = "https://api.fetch.tinyfish.ai"
DEFAULT_TINYFISH_FETCH_TIMEOUT_SECONDS = 150.0
WEATHERAPI_API_KEY_ENV = "WEATHERAPI_API_KEY"
WEATHERAPI_API_URL_ENV = "WEATHERAPI_API_URL"
WEATHERAPI_TIMEOUT_SECONDS_ENV = "WEATHERAPI_TIMEOUT_SECONDS"
DEFAULT_WEATHERAPI_API_URL = "https://api.weatherapi.com/v1"
DEFAULT_WEATHERAPI_TIMEOUT_SECONDS = 20.0


def _env(
    env: ToolEnv | Mapping[str, object] | None = None,
) -> ToolEnv:
    return resolve_tool_env(env=env)


def get_env(
    name: str,
    default: str = "",
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return get_tool_env(name, default, env=env)


def get_web_search_provider_override(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return get_env(OPENMINION_WEB_SEARCH_PROVIDER_ENV, "", env=env).strip().lower()


def get_tavily_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).tavily_api_key.strip()


def get_tavily_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return get_env(TAVILY_API_URL_ENV, "", env=env).strip()


def get_tavily_timeout_seconds(
    default: float = DEFAULT_TAVILY_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        TAVILY_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def get_brave_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).brave_api_key.strip()


def get_serpapi_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).serpapi_api_key.strip()


def get_serpapi_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        get_env(SERPAPI_API_URL_ENV, DEFAULT_SERPAPI_API_URL, env=env).strip()
        or DEFAULT_SERPAPI_API_URL
    )


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


def get_serper_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).serper_api_key.strip()


def get_serper_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        get_env(SERPER_API_URL_ENV, DEFAULT_SERPER_API_URL, env=env).strip()
        or DEFAULT_SERPER_API_URL
    )


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


def get_firecrawl_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).firecrawl_api_key.strip()


def get_firecrawl_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        get_env(FIRECRAWL_API_URL_ENV, DEFAULT_FIRECRAWL_API_URL, env=env).strip()
        or DEFAULT_FIRECRAWL_API_URL
    )


def get_firecrawl_timeout_seconds(
    default: float = DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        FIRECRAWL_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def get_tinyfish_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).tinyfish_api_key.strip()


def get_tinyfish_search_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        get_env(
            TINYFISH_SEARCH_API_URL_ENV,
            DEFAULT_TINYFISH_SEARCH_API_URL,
            env=env,
        ).strip()
        or DEFAULT_TINYFISH_SEARCH_API_URL
    )


def get_tinyfish_search_timeout_seconds(
    default: float = DEFAULT_TINYFISH_SEARCH_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        TINYFISH_SEARCH_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def get_tinyfish_fetch_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        get_env(
            TINYFISH_FETCH_API_URL_ENV,
            DEFAULT_TINYFISH_FETCH_API_URL,
            env=env,
        ).strip()
        or DEFAULT_TINYFISH_FETCH_API_URL
    )


def get_tinyfish_fetch_timeout_seconds(
    default: float = DEFAULT_TINYFISH_FETCH_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        TINYFISH_FETCH_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def get_weatherapi_api_key(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return _env(env).weatherapi_api_key.strip()


def get_weatherapi_api_url(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> str:
    return (
        get_env(WEATHERAPI_API_URL_ENV, DEFAULT_WEATHERAPI_API_URL, env=env).strip()
        or DEFAULT_WEATHERAPI_API_URL
    )


def get_weatherapi_timeout_seconds(
    default: float = DEFAULT_WEATHERAPI_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        WEATHERAPI_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
    )


def get_ip_public_lookup_endpoints(
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    return get_tool_env_list(
        OPENMINION_IP_PUBLIC_ENDPOINTS_ENV,
        default=DEFAULT_IP_PUBLIC_LOOKUP_ENDPOINTS,
        env=env,
    )


def get_ip_public_timeout_seconds(
    default: float = DEFAULT_IP_PUBLIC_TIMEOUT_SECONDS,
    *,
    env: ToolEnv | Mapping[str, object] | None = None,
) -> float:
    return get_tool_env_float(
        OPENMINION_IP_PUBLIC_TIMEOUT_SECONDS_ENV,
        float(default),
        env=env,
        minimum=0.5,
        maximum=30.0,
    )


__all__ = [
    "DEFAULT_FIRECRAWL_API_URL",
    "DEFAULT_FIRECRAWL_TIMEOUT_SECONDS",
    "DEFAULT_TINYFISH_FETCH_API_URL",
    "DEFAULT_TINYFISH_FETCH_TIMEOUT_SECONDS",
    "DEFAULT_TINYFISH_SEARCH_API_URL",
    "DEFAULT_TINYFISH_SEARCH_TIMEOUT_SECONDS",
    "DEFAULT_SERPAPI_API_URL",
    "DEFAULT_SERPAPI_TIMEOUT_SECONDS",
    "DEFAULT_SERPER_API_URL",
    "DEFAULT_SERPER_TIMEOUT_SECONDS",
    "DEFAULT_IP_PUBLIC_LOOKUP_ENDPOINTS",
    "DEFAULT_IP_PUBLIC_TIMEOUT_SECONDS",
    "DEFAULT_TAVILY_TIMEOUT_SECONDS",
    "DEFAULT_WEATHERAPI_API_URL",
    "DEFAULT_WEATHERAPI_TIMEOUT_SECONDS",
    "FIRECRAWL_API_URL_ENV",
    "FIRECRAWL_TIMEOUT_SECONDS_ENV",
    "OPENMINION_IP_PUBLIC_ENDPOINTS_ENV",
    "OPENMINION_IP_PUBLIC_TIMEOUT_SECONDS_ENV",
    "OPENMINION_WEB_SEARCH_PROVIDER_ENV",
    "SERPAPI_API_URL_ENV",
    "SERPAPI_TIMEOUT_SECONDS_ENV",
    "SERPER_API_URL_ENV",
    "SERPER_TIMEOUT_SECONDS_ENV",
    "TAVILY_API_URL_ENV",
    "TAVILY_TIMEOUT_SECONDS_ENV",
    "TINYFISH_FETCH_API_URL_ENV",
    "TINYFISH_FETCH_TIMEOUT_SECONDS_ENV",
    "TINYFISH_SEARCH_API_URL_ENV",
    "TINYFISH_SEARCH_TIMEOUT_SECONDS_ENV",
    "WEATHERAPI_API_KEY_ENV",
    "WEATHERAPI_API_URL_ENV",
    "WEATHERAPI_TIMEOUT_SECONDS_ENV",
    "get_env",
    "get_brave_api_key",
    "get_firecrawl_api_key",
    "get_firecrawl_api_url",
    "get_firecrawl_timeout_seconds",
    "get_ip_public_lookup_endpoints",
    "get_ip_public_timeout_seconds",
    "get_serpapi_api_key",
    "get_serpapi_api_url",
    "get_serpapi_timeout_seconds",
    "get_serper_api_key",
    "get_serper_api_url",
    "get_serper_timeout_seconds",
    "get_tavily_api_key",
    "get_tavily_api_url",
    "get_tavily_timeout_seconds",
    "get_tinyfish_api_key",
    "get_tinyfish_fetch_api_url",
    "get_tinyfish_fetch_timeout_seconds",
    "get_tinyfish_search_api_url",
    "get_tinyfish_search_timeout_seconds",
    "get_weatherapi_api_key",
    "get_weatherapi_api_url",
    "get_weatherapi_timeout_seconds",
    "get_web_search_provider_override",
]
