from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PinchTabConfigArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: Optional[str] = None
    token: Optional[str] = None
    timeout_sec: Optional[int] = Field(default=None, ge=1, le=300)
    max_retries: Optional[int] = Field(default=None, ge=1, le=10)
    backoff_ms: Optional[int] = Field(default=None, ge=0, le=10000)


class HealthArgs(PinchTabConfigArgs):
    pass


class InstanceStartArgs(PinchTabConfigArgs):
    profile_id: Optional[str] = None
    mode: Optional[Literal["headed", "headless"]] = None


class InstanceStopArgs(PinchTabConfigArgs):
    instance_id: str = Field(..., min_length=1)


class TabOpenArgs(PinchTabConfigArgs):
    instance_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)


class TabsListArgs(PinchTabConfigArgs):
    pass


class TabCloseArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)


class NavigateArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)


class SnapshotArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    summary_limit: int = Field(default=20, ge=1, le=200)
    include_snapshot: bool = False


class TextArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    mode: Literal["readability", "raw"] = "readability"
    include_text: bool = False


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


class ScreenshotArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)


class PdfArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)


class EvalArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    js: str = Field(..., min_length=1)
    store_artifact: bool = True


class ActionArgs(PinchTabConfigArgs):
    tab_id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    ref: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    option: Optional[str] = None
    delta: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
