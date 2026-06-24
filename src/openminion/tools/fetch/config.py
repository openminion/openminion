from dataclasses import dataclass


@dataclass(frozen=True)
class FetchToolConfig:
    enabled: bool = True


def load_config(*_args: object, **_kwargs: object) -> FetchToolConfig:
    return FetchToolConfig()


__all__ = ["FetchToolConfig", "load_config"]
