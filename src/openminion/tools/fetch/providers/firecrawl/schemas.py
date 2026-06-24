from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .constants import DEFAULT_FIRECRAWL_FORMATS, SUPPORTED_FIRECRAWL_FORMATS

FirecrawlFormat = Literal["markdown", "html", "rawHtml", "links"]


class FirecrawlProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formats: list[FirecrawlFormat] = Field(
        default_factory=lambda: list(DEFAULT_FIRECRAWL_FORMATS)
    )
    only_main_content: bool | None = None
    include_tags: list[str] | None = None
    exclude_tags: list[str] | None = None
    wait_for_ms: int | None = Field(default=None, ge=0, le=60_000)
    mobile: bool | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    block_ads: bool | None = None


__all__ = [
    "FirecrawlFormat",
    "FirecrawlProviderOptions",
    "DEFAULT_FIRECRAWL_FORMATS",
    "SUPPORTED_FIRECRAWL_FORMATS",
]
