from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScraplingProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "static", "dynamic", "stealth"] = "auto"
    headless: bool = True
    max_pages: int = Field(default=10, ge=1, le=100)
    solve_cloudflare: bool = False
    user_data_dir: str | None = Field(default=None, max_length=2048)
    geoip: bool = False
    google_search: bool = False
    additional_args: list[str] = Field(default_factory=list, max_length=32)


class FetchProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scrapling: ScraplingProviderOptions = Field(
        default_factory=ScraplingProviderOptions
    )
