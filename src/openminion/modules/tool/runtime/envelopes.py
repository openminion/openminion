"""Tool runtime envelopes and run-root creation."""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..contracts.schemas import (
    Artifact,
    ErrorInfo,
    LogEntry,
    ResultEnvelope,
    Scope,
    WorkspaceInfo,
)
from ..errors import ToolRuntimeError
from .policy import Policy


__all__ = [
    "create_run_root",
    "new_run_id",
    "make_ok_envelope",
    "make_error_envelope",
]


def create_run_root(
    policy: Policy, run_id: str, root_override: Optional[Path] = None
) -> Path:
    date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = (
        root_override.expanduser().resolve(strict=False)
        if root_override
        else policy.workspace_root()
    )
    run_root = root / date_dir / f"run_{run_id}"
    try:
        (run_root / "artifacts").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"Unable to create run directory: {run_root}",
            {"root": str(root), "run_root": str(run_root), "error": str(exc)},
        ) from exc
    return run_root


def new_run_id() -> str:
    return str(uuid.uuid4())


def make_ok_envelope(
    *,
    tool: str,
    run_id: str,
    request_id: Optional[str],
    scope: Scope,
    started_at: str,
    workspace: Path,
    artifacts: list[Artifact],
    logs: list[LogEntry],
    data: dict[str, Any],
) -> ResultEnvelope:
    started = datetime.fromisoformat(started_at)
    ended = datetime.now(timezone.utc)
    return ResultEnvelope(
        ok=True,
        tool=tool,
        run_id=run_id,
        request_id=request_id,
        policy_scope=scope,
        started_at=started_at,
        ended_at=ended.isoformat(),
        duration_ms=int((ended - started).total_seconds() * 1000),
        workspace=WorkspaceInfo(root=str(workspace), relative_root="."),
        artifacts=artifacts,
        logs=logs,
        data=data,
        error=None,
    )


def make_error_envelope(
    *,
    tool: str,
    run_id: str,
    request_id: Optional[str],
    scope: Scope,
    started_at: str,
    workspace: Path,
    artifacts: list[Artifact],
    logs: list[LogEntry],
    error: ToolRuntimeError,
) -> ResultEnvelope:
    started = datetime.fromisoformat(started_at)
    ended = datetime.now(timezone.utc)
    return ResultEnvelope(
        ok=False,
        tool=tool,
        run_id=run_id,
        request_id=request_id,
        policy_scope=scope,
        started_at=started_at,
        ended_at=ended.isoformat(),
        duration_ms=int((ended - started).total_seconds() * 1000),
        workspace=WorkspaceInfo(root=str(workspace), relative_root="."),
        artifacts=artifacts,
        logs=logs,
        data={},
        error=ErrorInfo(code=error.code, message=error.message, details=error.details),
    )
