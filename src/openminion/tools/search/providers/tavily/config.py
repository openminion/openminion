from dataclasses import dataclass

from .constants import DEFAULT_TAVILY_API_URL

DEFAULT_TAVILY_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class TavilySearchConfig:
    api_url: str = DEFAULT_TAVILY_API_URL
    timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS


def load_config(*_args: object, **_kwargs: object) -> TavilySearchConfig:
    return TavilySearchConfig()


__all__ = ["DEFAULT_TAVILY_TIMEOUT_SECONDS", "TavilySearchConfig", "load_config"]
