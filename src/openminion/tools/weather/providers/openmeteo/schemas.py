from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_CURRENT_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "weather_code",
    "wind_speed_10m",
]


class WeatherOpenMeteoUnitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: Literal["celsius", "fahrenheit"] = "celsius"
    wind_speed: Literal["kmh", "mph"] = "kmh"


class WeatherOpenMeteoCachingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    ttl_seconds: int = Field(default=300, ge=1, le=3600)
    key_mode: Literal["normalized_query"] = "normalized_query"


class WeatherOpenMeteoFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: Literal["static_samples", "disabled"] = "static_samples"


class WeatherOpenMeteoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    retries: int = Field(default=1, ge=0, le=3)
    default_language: str = "en"
    default_country_code: Optional[str] = None
    geocoding_count: int = Field(default=1, ge=1, le=10)
    timezone: str = "auto"
    current_fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CURRENT_FIELDS)
    )
    units: WeatherOpenMeteoUnitsConfig = Field(
        default_factory=WeatherOpenMeteoUnitsConfig
    )
    caching: WeatherOpenMeteoCachingConfig = Field(
        default_factory=WeatherOpenMeteoCachingConfig
    )
    fallback: WeatherOpenMeteoFallbackConfig = Field(
        default_factory=WeatherOpenMeteoFallbackConfig
    )
    debug: bool = False

    @field_validator("default_language", mode="before")
    @classmethod
    def _normalize_default_language(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        return normalized or "en"

    @field_validator("timezone", mode="before")
    @classmethod
    def _normalize_timezone(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        return normalized or "auto"

    @field_validator("default_country_code", mode="before")
    @classmethod
    def _normalize_country_code(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized.upper()

    @field_validator("current_fields", mode="before")
    @classmethod
    def _normalize_current_fields(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return list(DEFAULT_CURRENT_FIELDS)
        normalized = [str(item or "").strip() for item in value]
        rows = [item for item in normalized if item]
        return rows or list(DEFAULT_CURRENT_FIELDS)


class WeatherOpenMeteoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: Optional[str] = Field(
        default=None, description="Location query (city, postal code, etc.)"
    )
    city: Optional[str] = Field(default=None, description="Alias for location")
    query: Optional[str] = Field(default=None, description="Alias for location")
    place: Optional[str] = Field(default=None, description="Alias for location")
    latitude: Optional[float] = Field(default=None, description="Latitude coordinate")
    longitude: Optional[float] = Field(default=None, description="Longitude coordinate")
    lat: Optional[float] = Field(default=None, description="Latitude alias")
    lon: Optional[float] = Field(default=None, description="Longitude alias")
    language: Optional[str] = Field(default=None, description="Geocoding language")
    timeout_s: Optional[float] = Field(default=None, ge=1.0, le=60.0)
    debug: bool = Field(
        default=False, description="Capture raw upstream payload artifacts"
    )

    @field_validator("location", "city", "query", "place", "language", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("latitude", "longitude", "lat", "lon", mode="before")
    @classmethod
    def _normalize_optional_float(cls, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("coordinate must be numeric")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("coordinate must be numeric") from exc

    @model_validator(mode="after")
    def _validate_location_aliases(self) -> "WeatherOpenMeteoArgs":
        # Normalize coordinate aliases to canonical fields.
        if self.latitude is None and self.lat is not None:
            self.latitude = self.lat
        if self.longitude is None and self.lon is not None:
            self.longitude = self.lon

        any([self.location, self.city, self.query, self.place])
        has_coordinates = self.latitude is not None or self.longitude is not None
        if has_coordinates and (self.latitude is None or self.longitude is None):
            raise ValueError(
                "Both latitude and longitude are required when passing coordinates"
            )
        return self


WEATHER_OPENMETEO_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "Location query (city, postal code, etc.)",
        },
        "city": {"type": "string", "description": "Alias for location"},
        "query": {"type": "string", "description": "Alias for location"},
        "place": {"type": "string", "description": "Alias for location"},
        "latitude": {"type": "number", "description": "Latitude coordinate"},
        "longitude": {"type": "number", "description": "Longitude coordinate"},
        "lat": {"type": "number", "description": "Latitude alias"},
        "lon": {"type": "number", "description": "Longitude alias"},
        "language": {
            "type": "string",
            "description": "Geocoding language (default from config)",
        },
        "timeout_s": {"type": "number", "minimum": 1, "maximum": 60},
        "debug": {
            "type": "boolean",
            "description": "Capture raw upstream JSON artifacts",
        },
    },
    "additionalProperties": False,
}

WEATHER_OPENMETEO_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["location", "observed_at", "metrics", "source"],
    "properties": {
        "location": {
            "type": "object",
            "required": ["query", "resolved_name", "country", "latitude", "longitude"],
            "properties": {
                "query": {"type": "string"},
                "resolved_name": {"type": "string"},
                "country": {"type": "string"},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
            },
        },
        "observed_at": {
            "type": "string",
            "description": "ISO 8601 timestamp from Open-Meteo current.time",
        },
        "metrics": {
            "type": "object",
            "required": [
                "temperature_c",
                "humidity_pct",
                "wind_speed_kmh",
                "weather_code",
            ],
            "properties": {
                "temperature_c": {"type": "number"},
                "humidity_pct": {"type": "number"},
                "wind_speed_kmh": {"type": "number"},
                "weather_code": {"type": "number"},
            },
        },
        "summary": {"type": "string"},
        "source": {
            "type": "object",
            "required": ["provider", "endpoints", "license_note"],
            "properties": {
                "provider": {"type": "string", "enum": ["open-meteo"]},
                "geocoding_provider": {"type": "string", "enum": ["nominatim"]},
                "endpoints": {"type": "array", "items": {"type": "string"}},
                "license_note": {"type": "string"},
            },
        },
        "verified": {"type": "boolean"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
