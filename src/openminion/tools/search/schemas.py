from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import SEARCH_PROVIDER_AUTO


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, description="Search query text")
    q: str | None = Field(default=None, description="Alias for query")
    provider: str = Field(
        default=SEARCH_PROVIDER_AUTO,
        description="Provider selection: auto, tavily, brave, serpapi, firecrawl, serper, tinyfish",
    )
    max_results: int = Field(default=5, ge=1, le=20)
    count: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Alias for max_results",
    )
    search_depth: str | None = Field(default=None)
    include_answer: bool | None = Field(default=None)
    extra_snippets: bool | None = Field(default=None)
    country: str | None = Field(default=None)
    search_lang: str | None = Field(default=None)
    ui_lang: str | None = Field(default=None)
    safesearch: str | None = Field(default=None)
    offset: int | None = Field(default=None, ge=0, le=9)
    api_key: str | None = Field(default=None)

    @field_validator(
        "query",
        "q",
        "provider",
        "search_depth",
        "country",
        "search_lang",
        "ui_lang",
        "safesearch",
        "api_key",
        mode="before",
    )
    @classmethod
    def _normalize_optional_string(cls, value: Any) -> str | None:
        if value is None:
            return None
        return str(value).strip()

    @model_validator(mode="after")
    def _normalize_aliases(self) -> "SearchArgs":
        if not self.query and self.q:
            self.query = self.q
        if self.count is not None:
            self.max_results = self.count
        if not self.query:
            raise ValueError("query is required")
        self.provider = self.provider.lower() or SEARCH_PROVIDER_AUTO
        return self


class SearchResultItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    rank: int = Field(default=1, ge=1)
    title: str = Field(default="Untitled")
    url: str = Field(default="")
    description: str = Field(default="")


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str
    query: dict[str, Any]
    results: list[SearchResultItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    answer: str | None = None
