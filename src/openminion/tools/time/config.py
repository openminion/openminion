from dataclasses import dataclass

from .constants import DEFAULT_PARSE_TIMEZONE


@dataclass(frozen=True)
class TimeToolConfig:
    default_parse_timezone: str = DEFAULT_PARSE_TIMEZONE


def load_config(*_args: object, **_kwargs: object) -> TimeToolConfig:
    return TimeToolConfig()


__all__ = ["TimeToolConfig", "load_config"]
