from dataclasses import dataclass

from .constants import SEARCH_PROVIDER_AUTO


@dataclass(frozen=True)
class SearchToolConfig:
    default_provider: str = SEARCH_PROVIDER_AUTO


def load_config(*_args: object, **_kwargs: object) -> SearchToolConfig:
    return SearchToolConfig()


__all__ = ["SearchToolConfig", "load_config"]
