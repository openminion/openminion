from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FetchExtractArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: Literal["auto", "none", "text"] = "auto"
    selector: str | None = Field(default=None, max_length=512)


class FetchGetArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(..., min_length=1, max_length=4096)
    method: Literal["GET", "HEAD"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    accept: str = Field(default="text/html,text/plain,application/json", max_length=512)
    timeout_ms: int = Field(default=8000, ge=100, le=30000)
    max_bytes: int = Field(default=2_000_000, ge=1_024, le=10_000_000)
    max_redirects: int = Field(default=5, ge=0, le=10)
    follow_redirects: bool = True
    prefer_backend: str = Field(default="auto", max_length=64)
    backend: str | None = Field(default=None, max_length=64)
    render: Literal["none"] = "none"
    extract: FetchExtractArgs = Field(default_factory=FetchExtractArgs)
    provider_options: dict[str, Any] = Field(default_factory=dict)
    max_response_chars: int = Field(default=1200, ge=200, le=500_000)


class FetchHeadArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(..., min_length=1, max_length=4096)
    method: Literal["HEAD"] = "HEAD"
    headers: dict[str, str] = Field(default_factory=dict)
    accept: str = Field(default="text/html,text/plain,application/json", max_length=512)
    timeout_ms: int = Field(default=8000, ge=100, le=30000)
    max_bytes: int = Field(default=2_000_000, ge=1_024, le=10_000_000)
    max_redirects: int = Field(default=5, ge=0, le=10)
    follow_redirects: bool = True
    prefer_backend: str = Field(default="auto", max_length=64)
    backend: str | None = Field(default=None, max_length=64)
    provider_options: dict[str, Any] = Field(default_factory=dict)


class FetchProvidersArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
