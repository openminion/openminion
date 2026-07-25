# ruff: noqa: F403,F405
from .common import *
from .base import PinchTabConfigArgs

class ClickArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1)

class FillArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1)
    text: str = ""

class TypeArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1)
    text: str = ""

class PressArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1)
    ref: Optional[str] = None

class HoverArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1)

class SelectArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1)
    option: str = Field(..., min_length=1)

class ScrollArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    ref: Optional[str] = None
    delta: Optional[int] = None

class ActionArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    ref: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    option: Optional[str] = None
    delta: Optional[int] = None
    extra: dict[str, Any] = Field(default_factory=dict)
