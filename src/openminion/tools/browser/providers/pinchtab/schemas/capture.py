# ruff: noqa: F403,F405
from .common import *
from .base import PinchTabConfigArgs

class SnapshotArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    summary_limit: int = Field(default=20, ge=1, le=200)
    include_snapshot: bool = False

class TextArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    mode: Literal["readability", "raw"] = "readability"
    include_text: bool = False

class ScreenshotArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)

class PdfArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)

class EvalArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    js: str = Field(..., min_length=1)
    store_artifact: bool = True
