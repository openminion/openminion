# ruff: noqa: F403,F405
from .common import *


class PinchTabConfigArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: Optional[str] = None
    token: Optional[str] = None
    timeout_sec: Optional[int] = Field(default=None, ge=1, le=300)
    max_retries: Optional[int] = Field(default=None, ge=1, le=10)
    backoff_ms: Optional[int] = Field(default=None, ge=0, le=10000)


HealthArgs = PinchTabConfigArgs
