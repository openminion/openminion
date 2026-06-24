import json

from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
    ApprovalResponse,
)

HostMode = Literal["sandbox", "gateway", "node"]
SecurityMode = Literal["deny", "allowlist", "full"]
AskMode = Literal["off", "on-miss", "always"]
ExecStatus = Literal["ok", "error", "running", "approval-pending", "denied", "timeout"]

_EXEC_RUN_COMMAND_ALIASES = ("cmd", "command_line")
_EXEC_RUN_WORKDIR_ALIASES = ("cwd", "working_directory", "path")
_EXEC_RUN_TIMEOUT_ALIASES = (
    "timeout",
    "timeout_ms",
    "timeout_sec",
    "timeout_secs",
    "timeout_seconds",
    "max_duration",
)
_EXEC_RUN_IGNORED_METADATA_KEYS = ("daemon", "description", "stderr_to_stdout")
ProcessStatus = Literal["running", "exited", "killed"]
AckStatus = Literal["ok", "error"]


class ArtifactRefModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(..., min_length=1)
    kind: str = Field(default="file", min_length=1)
    name: str = Field(..., min_length=1)
    meta: Dict[str, Any] = Field(default_factory=dict)


class ExecErrorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class ExecMetricsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_ms: int = 0
    bytes_out: int = 0
    bytes_err: int = 0
    retries: int = 0


class ExecRunArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    command: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("command", "cmd"),
        description="Shell command to execute",
    )
    workdir: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workdir", *_EXEC_RUN_WORKDIR_ALIASES),
        description="Working directory relative to workspace root",
    )
    env: Dict[str, str] = Field(
        default_factory=dict, description="Environment overrides"
    )
    yield_ms: int = Field(default=10000, ge=0, le=3_600_000)
    background: bool = False
    timeout_s: int = Field(default=1800, ge=1, le=86_400)
    pty: bool = False
    host: HostMode = "sandbox"
    security: SecurityMode = "deny"
    ask: AskMode = "on-miss"
    ask_fallback: AskMode = "off"
    node: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            cleaned = dict(values)
            for alias in _EXEC_RUN_COMMAND_ALIASES:
                if "command" in cleaned and alias in cleaned:
                    cleaned.pop(alias, None)
                elif "command" not in cleaned and alias in cleaned:
                    cleaned["command"] = cleaned.pop(alias)
            for alias in _EXEC_RUN_WORKDIR_ALIASES:
                if "workdir" in cleaned and alias in cleaned:
                    cleaned.pop(alias, None)
                elif "workdir" not in cleaned and alias in cleaned:
                    cleaned["workdir"] = cleaned.pop(alias)
            for alias in _EXEC_RUN_TIMEOUT_ALIASES:
                if "timeout_s" in cleaned and alias in cleaned:
                    cleaned.pop(alias, None)
                elif "timeout_s" not in cleaned and alias in cleaned:
                    timeout_value = cleaned.pop(alias)
                    if alias == "timeout_ms":
                        try:
                            timeout_value = max(1, int(timeout_value) // 1000)
                        except (TypeError, ValueError):
                            pass
                    cleaned["timeout_s"] = timeout_value
            for key in _EXEC_RUN_IGNORED_METADATA_KEYS:
                cleaned.pop(key, None)
            return cleaned
        return values

    @field_validator("command")
    @classmethod
    def _normalize_command(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("command must not be empty")
        return normalized

    @field_validator("env", mode="before")
    @classmethod
    def _normalize_env(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            token = value.strip()
            if not token:
                return {}
            try:
                parsed = json.loads(token)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "env must be a JSON object when provided as text"
                ) from exc
            value = parsed
        if isinstance(value, dict):
            return {str(key): str(item) for key, item in value.items()}
        raise ValueError("env must be an object")

    @field_validator("node")
    @classmethod
    def _normalize_node(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("workdir", mode="before")
    @classmethod
    def _normalize_workdir(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class ExecRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ExecStatus
    risk_tier: ActionRiskTier = "silent"
    exit_code: Optional[int] = None
    session_id: Optional[str] = None
    approval_id: Optional[str] = None
    approval_response: Optional[ApprovalResponse] = None
    stdout_artifact: Optional[ArtifactRefModel] = None
    stderr_artifact: Optional[ArtifactRefModel] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    summary: str = Field(default="")
    metrics: ExecMetricsModel = Field(default_factory=ExecMetricsModel)
    error: Optional[ExecErrorModel] = None
    stdout_preview: Optional[str] = None
    stderr_preview: Optional[str] = None


class ProcessPollArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)
    tail_lines: int = Field(default=200, ge=1, le=5000)


class ProcessPollResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProcessStatus
    exit_code: Optional[int] = None
    new_stdout_artifact: Optional[ArtifactRefModel] = None
    new_stderr_artifact: Optional[ArtifactRefModel] = None
    summary: str = Field(default="")
    stdout_preview: Optional[str] = None
    stderr_preview: Optional[str] = None
    error: Optional[ExecErrorModel] = None


class ProcessSendKeysArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)
    keys: List[str] = Field(..., min_length=1)

    @field_validator("keys")
    @classmethod
    def _keys_non_empty(cls, value: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("keys must include at least one key")
        return cleaned


class ProcessSubmitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)


class ProcessPasteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)
    text: str = Field(..., description="Text to paste")
    bracketed: bool = True


class ProcessKillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)
    signal: Optional[str] = Field(
        default=None, description="TERM/KILL/INT or platform signal name"
    )


class ProcessClearArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)


class ProcessListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_exited: bool = False


class ProcessAckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: AckStatus
    summary: str = Field(default="")
    session_id: Optional[str] = None
    error: Optional[ExecErrorModel] = None


class ProcessListResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sessions: List[Dict[str, Any]] = Field(default_factory=list)


def tool_schemas() -> Dict[str, Dict[str, Any]]:
    return {
        "exec.run": {
            "description": "Run a shell command in workspace scope with optional PTY/background sessioning.",
            "args_schema": ExecRunArgs.model_json_schema(),
            "return_schema": ExecRunResult.model_json_schema(),
        },
        "exec.poll": {
            "description": "Poll a background exec session for status and new output.",
            "args_schema": ProcessPollArgs.model_json_schema(),
            "return_schema": ProcessPollResult.model_json_schema(),
        },
        "exec.send_keys": {
            "description": "Send tmux-like keys to a PTY session.",
            "args_schema": ProcessSendKeysArgs.model_json_schema(),
            "return_schema": ProcessAckResult.model_json_schema(),
        },
        "exec.submit": {
            "description": "Send carriage return (Enter) to a session.",
            "args_schema": ProcessSubmitArgs.model_json_schema(),
            "return_schema": ProcessAckResult.model_json_schema(),
        },
        "exec.paste": {
            "description": "Paste multi-line text into a session, bracketed by default.",
            "args_schema": ProcessPasteArgs.model_json_schema(),
            "return_schema": ProcessAckResult.model_json_schema(),
        },
        "exec.kill": {
            "description": "Terminate a managed background session.",
            "args_schema": ProcessKillArgs.model_json_schema(),
            "return_schema": ProcessAckResult.model_json_schema(),
        },
        "exec.clear": {
            "description": "Clear retained session metadata for an exited session.",
            "args_schema": ProcessClearArgs.model_json_schema(),
            "return_schema": ProcessAckResult.model_json_schema(),
        },
        "exec.list": {
            "description": "List managed sessions visible to the current agent.",
            "args_schema": ProcessListArgs.model_json_schema(),
            "return_schema": ProcessListResult.model_json_schema(),
        },
    }
