# ruff: noqa: F403,F405
from .common import *
from .base import PinchTabConfigArgs


class InstanceStartArgs(PinchTabConfigArgs):
    profile_id: Optional[str] = None
    mode: Optional[Literal["headed", "headless"]] = None


class InstanceStopArgs(PinchTabConfigArgs):
    instance_id: str = Field(..., min_length=1)
