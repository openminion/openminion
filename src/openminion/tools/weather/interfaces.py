from typing import TypedDict

WEATHER_PLUGIN_INTERFACE_VERSION = "v1"


class WeatherError(TypedDict, total=False):
    code: str
    message: str
    details: dict[str, str]


class WeatherResult(TypedDict, total=False):
    ok: bool
    provider: str
    location: str | None
    latitude: float | None
    longitude: float | None
    temperature_c: float | None
    temperature_f: float | None
    condition: str | None
    humidity_pct: float | None
    wind_kph: float | None
    observed_at: str | None
    warnings: list[str]
    error: WeatherError


__all__ = [
    "WEATHER_PLUGIN_INTERFACE_VERSION",
    "WeatherError",
    "WeatherResult",
]
