from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LocationGetArgs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "description": (
                "Resolve the current location. Calling location.get with no "
                "arguments means 'use my current location' with "
                'prefer="auto" and max_privacy="city". Only set fields when '
                "overriding the source preference or privacy cap."
            )
        },
    )

    prefer: Literal["auto", "identity", "ip", "session"] = Field(
        default="auto",
        description=(
            "Optional source preference for the current-location lookup. Leave "
            'it unset or use "auto" for the normal current-location behavior.'
        ),
    )
    max_privacy: Literal["none", "city", "region", "precise"] = Field(
        default="city",
        description=(
            "Optional privacy cap for the current-location result. Leave it "
            'unset or use "city" for the default current-location behavior.'
        ),
    )


class LocationSetDefaultArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(..., min_length=1, max_length=256)
    region: str | None = Field(default=None, max_length=256)
    country: str | None = Field(default=None, max_length=128)
    timezone: str | None = Field(default=None, max_length=128)
    privacy_level: Literal["none", "city", "region", "precise"] = "city"


class LocationGetIPArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_privacy: Literal["none", "city", "region", "precise"] = "city"
    refresh: bool = False
