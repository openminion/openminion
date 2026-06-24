from dataclasses import dataclass


@dataclass(frozen=True)
class FetchScraplingConfig:
    enabled: bool = True


def load_config(*_args: object, **_kwargs: object) -> FetchScraplingConfig:
    return FetchScraplingConfig()


__all__ = ["FetchScraplingConfig", "load_config"]
