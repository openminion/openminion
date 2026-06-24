from .schemas import WeatherOpenMeteoConfig


def load_config(payload: object | None = None) -> WeatherOpenMeteoConfig:
    return WeatherOpenMeteoConfig.model_validate(payload or {})


__all__ = ["WeatherOpenMeteoConfig", "load_config"]
