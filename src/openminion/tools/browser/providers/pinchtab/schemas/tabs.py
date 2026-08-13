# ruff: noqa: F403,F405
from .common import *
from .base import PinchTabConfigArgs


class TabOpenArgs(PinchTabConfigArgs):
    instance_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)


TabsListArgs = PinchTabConfigArgs


class TabCloseArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)


class NavigateArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
