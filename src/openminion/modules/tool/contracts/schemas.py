from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ErrorCode = Literal[
    "INVALID_ARGUMENT",
    "INVALID_REQUEST",
    "INVALID_RESPONSE",
    "POLICY_DENIED",
    "NOT_FOUND",
    "TIMEOUT",
    "EXEC_ERROR",
    "DEPENDENCY_MISSING",
    "PLATFORM_UNSUPPORTED",
    "AUTH_FAILED",
    "RATE_LIMITED",
    "UPSTREAM_ERROR",
    "INTERNAL_ERROR",
]

LogLevel = Literal["debug", "info", "warning", "error"]


class CallMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str | None = None
    dry_run: bool = False
    confirm: bool = False


class CallRequest(BaseModel):
    """JSON-RPC-like request payload for `openminion-tool call`."""

    model_config = ConfigDict(extra="forbid")
    tool: str = Field(..., min_length=1, description="Tool name like 'cmd.run'")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    meta: CallMeta = Field(default_factory=CallMeta)


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["file"] = "file"
    path: str
    mime: str
    bytes: int
    sha256: str
    canonical_ref: str | None = None


class LogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: str
    level: LogLevel
    msg: str
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkspaceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str
    relative_root: str = "."


class ResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    tool: str
    run_id: str
    request_id: str | None = None
    policy_scope: Scope
    started_at: str
    ended_at: str
    duration_ms: int
    workspace: WorkspaceInfo
    artifacts: list[Artifact] = Field(default_factory=list)
    logs: list[LogEntry] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


class FsListDirArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    recursive: bool = False
    max_entries: int | None = None
    include_hidden: bool = False
    pattern: str | None = None


class FsReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    max_bytes: int | None = None
    encoding: str = "utf-8"
    binary: bool = False


class FsWriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str | None = None
    base64: str | None = None
    mode: Literal["overwrite", "append", "create_only"] = "overwrite"
    mkdirs: bool = True
    atomic: bool = True


class FsCopyMoveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src: str
    dst: str
    overwrite: bool = False
    recursive: bool = True
    preserve_metadata: bool = False


class FsDeleteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    recursive: bool = False
    trash: bool = False
    confirm: bool = False


class FsSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str
    query: str
    regex: bool = False
    file_glob: str = "**/*"
    max_matches: int = 200


class CmdRunArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(..., min_length=1)
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    timeout_sec: int | None = None
    stdin: str | None = None
    capture: bool = True
    max_output_bytes: int | None = None
    allowed_exit_codes: list[int] = Field(default_factory=lambda: [0])


class CmdWhichArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class ProcListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contains: str | None = None
    user_only: bool = True
    limit: int = 200
    sort_by: Literal["cpu", "mem", "pid", "name"] = "cpu"


class ProcDetailsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: int
    include_open_files: bool = False
    include_connections: bool = False


class ProcKillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: int
    signal: Literal["TERM", "KILL"] = "TERM"
    confirm: bool = False


class SysInfoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_disks: bool = True
    include_net_ifaces: bool = False


if TYPE_CHECKING:
    # Canonical ownership lives in registry.py.
    from openminion.modules.tool.registry import Scope as Scope
else:
    # Runtime mirror keeps import graph cycle-free during transition.
    Scope = Literal["READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"]
