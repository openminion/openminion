from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openminion.modules.tool.contracts.model_ids import MODEL_WEATHER
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.routing import (
    resolve_runtime_provider_chain,
    resolve_runtime_tool_family_config,
)

from .constants import DEFAULT_WEATHER_PROVIDER_ID
from .providers import provider_registry, register_provider

_CANONICAL_TOOL = MODEL_WEATHER
_MISSING_LOCATION_TOKENS = frozenset({"none", "null"})
_WEATHER_CODE_LABELS: dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "cloudy",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


class WeatherArgs(BaseModel):
    """Current weather lookup arguments."""

    # Core schema is intentionally minimal; extra provider-specific keys are
    # preserved in extension_args and passed to provider implementations.
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {"location": "San Francisco"},
                {"location": "Tokyo", "language": "ja"},
                {"latitude": 37.7749, "longitude": -122.4194},
            ]
        },
    )

    provider: str = Field(default="auto")

    location: str | None = Field(
        default=None,
        description=(
            "Preferred place query. When the user names a city or place, copy it "
            "here explicitly. Required unless both latitude and longitude are "
            "provided. Never pass null, empty strings, or the literal 'None'."
        ),
    )
    city: str | None = Field(
        default=None,
        description="Legacy alias for location. Prefer location.",
    )
    query: str | None = Field(
        default=None,
        description="Legacy alias for location. Prefer location.",
    )
    place: str | None = Field(
        default=None,
        description="Legacy alias for location. Prefer location.",
    )

    latitude: float | None = Field(
        default=None,
        description="Latitude coordinate. Only use with longitude.",
    )
    longitude: float | None = Field(
        default=None,
        description="Longitude coordinate. Only use with latitude.",
    )
    lat: float | None = Field(default=None, description="Latitude alias")
    lon: float | None = Field(default=None, description="Longitude alias")

    language: str | None = Field(default=None, description="Provider language hint")
    timeout_s: float | None = Field(default=None, ge=1.0, le=60.0)
    debug: bool = Field(default=False)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        return token or "auto"

    @field_validator("location", "city", "query", "place", "language", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if normalized.lower() in _MISSING_LOCATION_TOKENS:
            return None
        return normalized or None


class _LegacyOpenMeteoCompatProvider:
    provider_id = DEFAULT_WEATHER_PROVIDER_ID

    def lookup(
        self,
        *,
        query_args: Mapping[str, Any],
        extension_args: Mapping[str, Any],
        ctx: RuntimeContext,
    ) -> Mapping[str, Any]:
        del extension_args
        from openminion.tools.weather.providers.openmeteo.plugin import (
            _h_weather_openmeteo_current,
        )

        return _h_weather_openmeteo_current(dict(query_args), ctx)

    def healthcheck(self) -> bool:
        return True


def _ensure_default_provider_registered() -> None:
    registry = provider_registry()
    if registry.get(DEFAULT_WEATHER_PROVIDER_ID) is not None:
        return
    register_provider(_LegacyOpenMeteoCompatProvider())


def _normalize_query_payload(
    validated: WeatherArgs,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload = validated.model_dump(exclude_none=True)
    requested_provider = str(payload.pop("provider", "auto") or "auto").strip().lower()

    extension_args = dict(validated.model_extra or {})

    query_args: dict[str, Any] = {}

    location = ""
    for key in ("location", "city", "query", "place"):
        token = str(payload.get(key, "") or "").strip()
        if token:
            location = token
            break
    if location:
        query_args["location"] = location

    latitude = payload.get("latitude")
    if latitude is None:
        latitude = payload.get("lat")
    longitude = payload.get("longitude")
    if longitude is None:
        longitude = payload.get("lon")

    if latitude is not None:
        query_args["latitude"] = float(latitude)
    if longitude is not None:
        query_args["longitude"] = float(longitude)

    has_lat = "latitude" in query_args
    has_lon = "longitude" in query_args
    if has_lat != has_lon:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Both latitude and longitude are required when passing coordinates",
        )

    for key in ("language", "timeout_s", "debug"):
        if key in payload:
            query_args[key] = payload[key]

    return requested_provider, query_args, extension_args


def _provider_chain(requested_provider: str, ctx: RuntimeContext) -> list[str]:
    registry = provider_registry()
    available = list(registry.list_provider_ids())
    if not available:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "No weather providers are registered",
        )

    if requested_provider not in {"", "auto"}:
        if requested_provider not in available:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Unsupported weather provider '{requested_provider}'",
                {"supported_provider": sorted(available)},
            )
        return [requested_provider]

    return resolve_runtime_provider_chain(
        available=available,
        family_config=resolve_runtime_tool_family_config(ctx, family_name="weather"),
    )


def _summary_condition(result: Mapping[str, Any]) -> str:
    summary = str(result.get("summary", "") or "").strip()
    lowered = summary.lower()
    if summary and all(
        marker not in lowered
        for marker in ("humidity ", "wind ", "observed_at=", "code ")
    ):
        return summary.rstrip(".")

    metrics = result.get("metrics")
    if isinstance(metrics, Mapping):
        weather_code = metrics.get("weather_code")
        try:
            code = int(float(weather_code))
        except (TypeError, ValueError):
            code = None
        if code is not None:
            return _WEATHER_CODE_LABELS.get(code, "")
    return ""


def _compact_summary(result: Mapping[str, Any]) -> str:
    location = result.get("location")
    metrics = result.get("metrics")
    if not isinstance(location, Mapping) or not isinstance(metrics, Mapping):
        return str(result.get("summary", "") or "").strip()

    resolved_name = str(location.get("resolved_name", "") or "").strip()
    country = str(location.get("country", "") or "").strip()
    place = resolved_name or str(location.get("query", "") or "").strip() or "unknown"
    if country:
        place = f"{place}, {country}"

    temperature = metrics.get("temperature_c")
    try:
        temp_text = f"{float(temperature):.1f}\N{DEGREE SIGN}C"
    except (TypeError, ValueError):
        temp_text = ""

    condition = _summary_condition(result)
    if temp_text and condition:
        return f"{place}: {temp_text}, {condition}."
    if temp_text:
        return f"{place}: {temp_text}."
    if condition:
        return f"{place}: {condition}."
    return str(result.get("summary", "") or "").strip()


def _execute_provider(
    *,
    requested_provider: str,
    query_args: Mapping[str, Any],
    extension_args: Mapping[str, Any],
    ctx: RuntimeContext,
) -> dict[str, Any]:
    registry = provider_registry()
    warnings: list[str] = []

    for provider_id in _provider_chain(requested_provider, ctx):
        provider = registry.get(provider_id)
        if provider is None:
            continue

        healthcheck = getattr(provider, "healthcheck", None)
        if callable(healthcheck):
            try:
                if not bool(healthcheck()):
                    warnings.append(f"provider '{provider_id}' reported unhealthy")
                    continue
            except Exception as exc:
                warnings.append(f"provider '{provider_id}' healthcheck failed: {exc}")
                continue

        try:
            payload = provider.lookup(
                query_args=query_args,
                extension_args=extension_args,
                ctx=ctx,
            )
        except ToolRuntimeError:
            raise
        except Exception as exc:
            warnings.append(f"provider '{provider_id}' execution failed: {exc}")
            continue

        if not isinstance(payload, Mapping):
            warnings.append(f"provider '{provider_id}' returned invalid payload")
            continue

        result = dict(payload)
        compact_summary = _compact_summary(result)
        if compact_summary:
            result["summary"] = compact_summary
        source = result.get("source")
        if isinstance(source, Mapping):
            source_payload = dict(source)
            source_payload.setdefault("provider_id", provider_id)
            result["source"] = source_payload
        else:
            # TGFC: ensure every successful weather lookup carries the
            result["source"] = {"provider_id": provider_id}
        if warnings:
            combined = (
                list(result.get("warnings", []))
                if isinstance(result.get("warnings"), list)
                else []
            )
            combined.extend(warnings)
            result["warnings"] = combined
        return result

    raise ToolRuntimeError(
        "UPSTREAM_ERROR",
        "No weather provider could satisfy this request",
        {"warnings": warnings},
    )


def _h_weather(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = WeatherArgs.model_validate(args or {})
    requested_provider, query_args, extension_args = _normalize_query_payload(validated)
    return _execute_provider(
        requested_provider=requested_provider,
        query_args=query_args,
        extension_args=extension_args,
        ctx=ctx,
    )


def register(registry: ToolRegistry) -> None:
    _ensure_default_provider_registered()
    registry.add(
        ToolSpec(
            name=_CANONICAL_TOOL,
            args_model=WeatherArgs,
            min_scope="READ_ONLY",
            handler=_h_weather,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "weather"),
            capabilities=("read_only", "network", "weather", "time_sensitive"),
        )
    )


__all__ = [
    "WeatherArgs",
    "register",
    "register_provider",
]
