from dataclasses import dataclass

from .constants import DEFAULT_WEATHER_PROVIDER_ID


@dataclass(frozen=True)
class WeatherToolConfig:
    default_provider_id: str = DEFAULT_WEATHER_PROVIDER_ID


def load_config(*_args: object, **_kwargs: object) -> WeatherToolConfig:
    return WeatherToolConfig()


__all__ = ["WeatherToolConfig", "load_config"]
