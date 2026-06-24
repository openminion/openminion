from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrowserOp(str, Enum):
    DAEMON_ENSURE = "daemon.ensure"
    INSTANCE_START = "instance.start"
    INSTANCE_LIST = "instance.list"
    INSTANCE_STOP = "instance.stop"
    INSTANCE_KILL = "instance.kill"
    TAB_NEW = "tab.new"
    TAB_LIST = "tab.list"
    TAB_SELECT = "tab.select"
    TAB_CLOSE = "tab.close"
    TAB_NAVIGATE = "tab.navigate"
    TAB_SNAPSHOT = "tab.snapshot"
    TAB_TEXT = "tab.text"
    TAB_ACTION = "tab.action"
    TAB_ACTIONS = "tab.actions"
    TAB_SCREENSHOT = "tab.screenshot"
    TAB_PDF = "tab.pdf"
    TAB_LOCK = "tab.lock"
    TAB_UNLOCK = "tab.unlock"


SUPPORTED_OPS: tuple[str, ...] = tuple(op.value for op in BrowserOp)


def normalize_op(op: str) -> str:
    return str(op or "").strip()


class BrowserCapabilities(BaseModel):
    snapshot_refs: bool = False
    selector_actions: bool = False
    batch_actions: bool = False
    pdf_export: bool = False
    cookies: bool = False
    js_evaluate: bool = False
    tab_locking: bool = False
    persistent_profiles: bool = False
    headed_mode: bool = False
    downloads: bool = False
    screenshot: bool = False
    text: bool = False
    selectors: bool = False
    role_selectors: bool = False
    trace: bool = False
    network_intercept: bool = False

    model_config = ConfigDict(extra="forbid")


class RoleTarget(BaseModel):
    role: str
    name: str | None = None
    exact: bool = False

    model_config = ConfigDict(extra="forbid")


class ActionTarget(BaseModel):
    ref: str | None = None
    selector: str | None = None
    role: RoleTarget | None = None

    model_config = ConfigDict(extra="allow")


class BrowserAction(BaseModel):
    kind: str
    target: ActionTarget | None = None
    text: str | None = None
    key: str | None = None
    option: str | None = None
    delta: int | None = None
    timeout_ms: int | None = None
    extra: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class SnapshotOptions(BaseModel):
    mode: str = "auto"
    compact: bool = True
    interactive: bool = True
    max_nodes: int = 800
    max_text_chars: int = 20000
    depth: int | None = None
    max_tokens: int | None = None

    model_config = ConfigDict(extra="allow")


class OutputOptions(BaseModel):
    path: str | None = None
    format: str | None = None
    quality: int | None = None

    model_config = ConfigDict(extra="forbid")


class InstanceSpec(BaseModel):
    profile: str | None = None
    mode: str | None = None
    port: int | None = None
    user_data_dir: str | None = None
    downloads_path: str | None = None

    model_config = ConfigDict(extra="allow")


class NavigateOptions(BaseModel):
    timeout_ms: int | None = None
    wait_until: str | None = None

    model_config = ConfigDict(extra="allow")


class TextOptions(BaseModel):
    mode: str = "readability"
    include_text: bool = True
    max_chars: int | None = None

    model_config = ConfigDict(extra="allow")


class BrowserCallArgs(BaseModel):
    op: str
    provider: str | None = None
    instance_id: str | None = None
    tab_id: str | None = None
    url: str | None = None

    instance: InstanceSpec | None = None
    profile: str | None = None
    mode: str | None = None
    port: int | None = None

    snapshot: SnapshotOptions | None = None
    text: TextOptions | None = None
    navigation: NavigateOptions | None = None
    action: BrowserAction | None = None
    actions: list[BrowserAction] = Field(default_factory=list)
    output: OutputOptions | None = None
    owner: str | None = None
    ttl_s: int | None = None
    options: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_ops(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload["op"] = normalize_op(str(payload.get("op", "")))
        return payload

    @model_validator(mode="after")
    def _validate_op(self) -> "BrowserCallArgs":
        if self.op not in SUPPORTED_OPS:
            raise ValueError(f"unsupported browser op '{self.op}'")
        return self


class InstanceInfo(BaseModel):
    id: str
    profile: str | None = None
    mode: str | None = None

    model_config = ConfigDict(extra="allow")


class TabInfo(BaseModel):
    id: str
    url: str = ""
    title: str = ""

    model_config = ConfigDict(extra="allow")


class SnapshotResult(BaseModel):
    format: str = "auto"
    nodes: list[Any] = Field(default_factory=list)
    interactive_refs: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class TextResult(BaseModel):
    content: str = ""
    truncated: bool = False
    chars: int = 0

    model_config = ConfigDict(extra="allow")


class ArtifactRef(BaseModel):
    kind: str
    path: str = ""
    sha256: str | None = None
    mime: str | None = None
    content_base64: str | None = None

    model_config = ConfigDict(extra="allow")


class BrowserError(BaseModel):
    code: str
    message: str
    provider_id: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class BrowserResult(BaseModel):
    provider: str
    capabilities: BrowserCapabilities = Field(default_factory=BrowserCapabilities)
    instance: InstanceInfo | None = None
    instances: list[InstanceInfo] = Field(default_factory=list)
    tab: TabInfo | None = None
    tabs: list[TabInfo] = Field(default_factory=list)
    snapshot: SnapshotResult | None = None
    text: TextResult | None = None
    artifact: ArtifactRef | None = None
    error: BrowserError | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")
