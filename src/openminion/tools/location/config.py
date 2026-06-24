from dataclasses import dataclass

LOCATION_IP_FALLBACK_URL = "https://ipapi.co/json/"
LOCATION_IP_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.15, 0.35, 0.7)


@dataclass(frozen=True)
class LocationToolConfig:
    enabled: bool = True


def load_config(*_args: object, **_kwargs: object) -> LocationToolConfig:
    return LocationToolConfig()


__all__ = [
    "LOCATION_IP_FALLBACK_URL",
    "LOCATION_IP_RETRY_BACKOFF_SECONDS",
    "LocationToolConfig",
    "load_config",
]
