from dataclasses import dataclass


@dataclass(frozen=True)
class DuckDuckGoSearchProviderConfig:
    endpoint: str = ""
    timeout_s: float = 0.0


def load_config(*_args: object, **_kwargs: object) -> DuckDuckGoSearchProviderConfig:
    return DuckDuckGoSearchProviderConfig()


__all__ = ["DuckDuckGoSearchProviderConfig", "load_config"]
