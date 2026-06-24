from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TinyFishProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["markdown", "html", "json"] = "markdown"
    links: bool = False
    image_links: bool = False


class FetchProviderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tinyfish: TinyFishProviderOptions = Field(default_factory=TinyFishProviderOptions)
