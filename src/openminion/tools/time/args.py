from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import (
    DEFAULT_NEXT_CRON_COUNT,
    MAX_NEXT_CRON_COUNT,
    TIME_DIFF_UNIT_SECONDS,
    TIME_FORMAT_ISO,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_text(value: Any, message: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(message)
    return token


class _RequiredISOArgs(_StrictModel):
    iso: str = Field(..., min_length=1, description="ISO8601 timestamp")

    @field_validator("iso", mode="before")
    @classmethod
    def _normalize_iso(cls, value: Any) -> str:
        return _required_text(value, "iso is required")


class TimeNowArgs(_StrictModel):
    timezone: str | None = Field(
        default=None,
        description="IANA timezone. Defaults to identity profile timezone or UTC.",
    )
    location: str | None = Field(
        default=None,
        description="Named place or city to resolve to a timezone when timezone is omitted.",
    )

    @field_validator("timezone", "location", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        token = str(value).strip()
        return token or None


class TimeInZoneArgs(_StrictModel):
    timezone: str = Field(..., min_length=1, description="IANA timezone")

    @field_validator("timezone", mode="before")
    @classmethod
    def _normalize_timezone(cls, value: Any) -> str:
        return _required_text(value, "timezone is required")


class TimeConvertArgs(_RequiredISOArgs):
    to_timezone: str = Field(..., min_length=1, description="IANA timezone")

    @field_validator("to_timezone", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return _required_text(value, "field is required")


class TimeParseISOArgs(_RequiredISOArgs):
    timezone_hint: str | None = Field(
        default=None,
        description="IANA timezone when ISO has no offset/Z",
    )


class TimeDiffArgs(_StrictModel):
    a: str = Field(..., min_length=1, description="ISO8601 timestamp")
    b: str = Field(..., min_length=1, description="ISO8601 timestamp")
    unit: str = Field(
        default=TIME_DIFF_UNIT_SECONDS,
        description="seconds|minutes|hours|days",
    )
    abs: bool = Field(default=True, description="Absolute delta")

    @field_validator("a", "b", mode="before")
    @classmethod
    def _normalize_iso(cls, value: Any) -> str:
        return _required_text(value, "timestamp is required")

    @field_validator("unit", mode="before")
    @classmethod
    def _normalize_unit(cls, value: Any) -> str:
        return (
            str(value or TIME_DIFF_UNIT_SECONDS).strip().lower()
            or TIME_DIFF_UNIT_SECONDS
        )


class TimeFormatArgs(_RequiredISOArgs):
    timezone: str | None = Field(default=None, description="IANA timezone")
    format: str = Field(
        default=TIME_FORMAT_ISO,
        description="iso|rfc3339|date|time|datetime|custom",
    )
    custom: str | None = Field(
        default=None, description="strftime pattern if format=custom"
    )

    @field_validator("format", mode="before")
    @classmethod
    def _normalize_format(cls, value: Any) -> str:
        return str(value or TIME_FORMAT_ISO).strip().lower() or TIME_FORMAT_ISO


class TimeDayBoundaryArgs(_StrictModel):
    iso: str | None = Field(
        default=None, description="ISO8601 timestamp; defaults to now"
    )
    timezone: str | None = Field(default=None, description="IANA timezone")


class TimeNextCronArgs(_StrictModel):
    cron: str = Field(..., min_length=1, description="5-field cron expression")
    timezone: str = Field(..., min_length=1, description="IANA timezone")
    from_iso: str | None = Field(
        default=None, description="Start point ISO8601; defaults to now"
    )
    count: int = Field(
        default=DEFAULT_NEXT_CRON_COUNT,
        ge=1,
        le=MAX_NEXT_CRON_COUNT,
        description="Number of occurrences",
    )

    @field_validator("cron", "timezone", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return _required_text(value, "field is required")
