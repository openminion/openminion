from typing import Literal, TypedDict

LOCATION_PLUGIN_INTERFACE_VERSION = "v1"

LocationSource = Literal["session.override", "identity.default", "ip.geo", "none"]
LocationPrivacyLevel = Literal["none", "city", "region", "precise"]
LocationConfidence = Literal["high", "medium", "low"]

LOCATION_SOURCE_VALUES: tuple[LocationSource, ...] = (
    "session.override",
    "identity.default",
    "ip.geo",
    "none",
)


class LocationError(TypedDict, total=False):
    code: str
    message: str
    details: dict[str, str]


class LocationResult(TypedDict, total=False):
    ok: bool
    source: LocationSource
    privacy_level: LocationPrivacyLevel
    confidence: LocationConfidence
    city: str | None
    region: str | None
    country: str | None
    timezone: str | None
    lat: float | None
    lon: float | None
    observed_at: str | None
    warnings: list[str]
    error: LocationError


__all__ = [
    "LOCATION_PLUGIN_INTERFACE_VERSION",
    "LOCATION_SOURCE_VALUES",
    "LocationConfidence",
    "LocationError",
    "LocationPrivacyLevel",
    "LocationResult",
    "LocationSource",
]
