from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Optional[str] = Field(default=None, description="Search query text")
    q: Optional[str] = Field(default=None, description="Alias for query")
    provider: str = Field(
        default="auto",
        description="Provider selection: auto, tavily, brave, serpapi, firecrawl, serper, tinyfish",
    )
    max_results: int = Field(default=5, ge=1, le=20)
    count: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="Alias for max_results",
    )
    search_depth: Optional[str] = Field(default=None)
    include_answer: Optional[bool] = Field(default=None)
    extra_snippets: Optional[bool] = Field(default=None)
    country: Optional[str] = Field(default=None)
    search_lang: Optional[str] = Field(default=None)
    ui_lang: Optional[str] = Field(default=None)
    safesearch: Optional[str] = Field(default=None)
    offset: Optional[int] = Field(default=None, ge=0, le=9)
    api_key: Optional[str] = Field(default=None)

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
    def _normalize_optional_string(cls, value: Any) -> Optional[str] | str:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized

    @model_validator(mode="after")
    def _normalize_aliases(self) -> "SearchArgs":
        if not self.query and self.q:
            self.query = self.q
        if self.count is not None:
            self.max_results = int(self.count)
        if not self.query:
            raise ValueError("query is required")
        self.provider = str(self.provider or "auto").strip().lower() or "auto"
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
    answer: Optional[str] = None
