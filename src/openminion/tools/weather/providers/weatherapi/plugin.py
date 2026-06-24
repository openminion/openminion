from openminion.modules.tool.registry import ToolRegistry

from .provider import WeatherApiProvider


def register(registry: ToolRegistry) -> None:
    from openminion.tools.weather import register_provider as register_weather_provider

    del registry
    register_weather_provider(WeatherApiProvider())


__all__ = ["register"]
