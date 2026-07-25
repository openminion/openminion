# ruff: noqa: F403,F405
from .common import *
class ProfileCostHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_1k: Optional[float] = None
    output_per_1k: Optional[float] = None

class ProfileCapabilities(BaseModel):
    model_config = ConfigDict(extra="allow")

    supports_json: bool = False
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = False
    supports_prompt_caching: bool = False

class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    endpoint: Optional[str] = None
    auth_ref: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    capabilities: ProfileCapabilities = Field(default_factory=ProfileCapabilities)
    supports_json: bool = False
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = False
    supports_prompt_caching: bool = False
    cost_hint: Optional[ProfileCostHint] = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_capabilities(self) -> "ProviderProfile":
        caps = self.capabilities
        self.supports_json = bool(self.supports_json or caps.supports_json)
        self.supports_tools = bool(self.supports_tools or caps.supports_tools)
        self.supports_vision = bool(self.supports_vision or caps.supports_vision)
        self.supports_streaming = bool(
            self.supports_streaming or caps.supports_streaming
        )
        self.supports_prompt_caching = bool(
            self.supports_prompt_caching or caps.supports_prompt_caching
        )
        return self
