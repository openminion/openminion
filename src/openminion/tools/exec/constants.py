from typing import Final, Literal

from openminion.tools.constants import (
    OPENMINION_POLICY_PATH_ENV as EXEC_POLICY_PATH_ENV,
)

EXEC_ENABLE_HOST_EXEC_ENV = "OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC"
EXEC_DEBUG_PARSE_EVENT_ENV = "OPENMINION_TOOL_EXEC_DEBUG_PARSE_EVENT"
EXEC_ALLOWLIST_PATHS_ENV = "OPENMINION_TOOL_EXEC_ALLOWLIST_PATHS"

EXEC_AGENT_ID_ENV = "OPENMINION_AGENT_ID"
EXEC_SAFE_BINS_ENV = "OPENMINION_TOOL_EXEC_SAFE_BINS"
EXEC_SAFE_BIN_TRUSTED_DIRS_ENV = "OPENMINION_TOOL_EXEC_SAFE_BIN_TRUSTED_DIRS"

EXEC_ARTIFACT_THRESHOLD_BYTES = 4096
EXEC_MAX_PREVIEW_CHARS = 4000
EXEC_SECURITY_MODE_DENY: Final[Literal["deny"]] = "deny"
EXEC_SECURITY_MODE_ALLOWLIST: Final[Literal["allowlist"]] = "allowlist"
EXEC_SECURITY_MODE_FULL: Final[Literal["full"]] = "full"
EXEC_ASK_MODE_OFF: Final[Literal["off"]] = "off"
EXEC_ASK_MODE_ON_MISS: Final[Literal["on-miss"]] = "on-miss"
EXEC_ASK_MODE_ALWAYS: Final[Literal["always"]] = "always"
EXEC_PROCESS_STATUS_RUNNING: Final[Literal["running"]] = "running"
EXEC_PROCESS_STATUS_EXITED: Final[Literal["exited"]] = "exited"
EXEC_PROCESS_STATUS_KILLED: Final[Literal["killed"]] = "killed"
EXEC_STATUS_OK: Final[Literal["ok"]] = "ok"
EXEC_STATUS_ERROR: Final[Literal["error"]] = "error"
EXEC_STATUS_RUNNING: Final[Literal["running"]] = "running"
EXEC_STATUS_APPROVAL_PENDING: Final[Literal["approval-pending"]] = "approval-pending"
EXEC_STATUS_DENIED: Final[Literal["denied"]] = "denied"
EXEC_STATUS_TIMEOUT: Final[Literal["timeout"]] = "timeout"
EXEC_SAFE_BINS_DEFAULT: frozenset[str] = frozenset(
    {
        "awk",
        "cat",
        "cut",
        "grep",
        "head",
        "sed",
        "sort",
        "tail",
        "tr",
        "uniq",
        "wc",
        "whoami",
    }
)
EXEC_SAFE_BIN_TRUSTED_DIRS_DEFAULT: frozenset[str] = frozenset(
    {"/bin", "/usr/bin", "/usr/local/bin"}
)
EXEC_DENY_HOST_ENV_PREFIXES: tuple[str, ...] = ("LD_", "DYLD_")
EXEC_APPROVAL_PENDING_STATUSES: frozenset[str] = frozenset({"on-miss", "always"})

__all__ = [
    "EXEC_AGENT_ID_ENV",
    "EXEC_ALLOWLIST_PATHS_ENV",
    "EXEC_ASK_MODE_ALWAYS",
    "EXEC_ASK_MODE_OFF",
    "EXEC_ASK_MODE_ON_MISS",
    "EXEC_APPROVAL_PENDING_STATUSES",
    "EXEC_ARTIFACT_THRESHOLD_BYTES",
    "EXEC_DEBUG_PARSE_EVENT_ENV",
    "EXEC_DENY_HOST_ENV_PREFIXES",
    "EXEC_ENABLE_HOST_EXEC_ENV",
    "EXEC_MAX_PREVIEW_CHARS",
    "EXEC_POLICY_PATH_ENV",
    "EXEC_PROCESS_STATUS_EXITED",
    "EXEC_PROCESS_STATUS_KILLED",
    "EXEC_PROCESS_STATUS_RUNNING",
    "EXEC_SAFE_BINS_DEFAULT",
    "EXEC_SAFE_BIN_TRUSTED_DIRS_DEFAULT",
    "EXEC_SAFE_BINS_ENV",
    "EXEC_SAFE_BIN_TRUSTED_DIRS_ENV",
    "EXEC_SECURITY_MODE_ALLOWLIST",
    "EXEC_SECURITY_MODE_DENY",
    "EXEC_SECURITY_MODE_FULL",
    "EXEC_STATUS_APPROVAL_PENDING",
    "EXEC_STATUS_DENIED",
    "EXEC_STATUS_ERROR",
    "EXEC_STATUS_OK",
    "EXEC_STATUS_RUNNING",
    "EXEC_STATUS_TIMEOUT",
]
