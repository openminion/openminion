import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from openminion.base.runtime.sandbox import (
    ExecResult as SandboxExecResult,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
)
from openminion.modules.tool.runtime.context import (
    RuntimeContext,
    preferred_artifact_ref,
)
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
    pending_approval_decision,
)

from .constants import (
    EXEC_APPROVAL_PENDING_STATUSES,
    EXEC_ARTIFACT_THRESHOLD_BYTES,
    EXEC_MAX_PREVIEW_CHARS,
)
from .command_parser import CommandParseError, parse_command
from .process import resolve_shell_family
from .schemas import (
    ExecErrorModel,
    ExecMetricsModel,
    ExecRunResult,
)

from .events import (
    _declared_exec_risk_tier,
    _emit_exec_operation,
    _slug,
    _timestamp_token,
)


_ARTIFACT_THRESHOLD_BYTES = EXEC_ARTIFACT_THRESHOLD_BYTES
_APPROVAL_PENDING_STATUSES = EXEC_APPROVAL_PENDING_STATUSES


def _command_summary(
    *,
    exit_code: int | None,
    stdout_preview: str | None,
    stderr_preview: str | None,
) -> str:
    lines = [f"Command exited with code {exit_code}."]
    if stdout_preview:
        lines.extend(("", "stdout:", stdout_preview.rstrip()))
    if stderr_preview:
        lines.extend(("", "stderr:", stderr_preview.rstrip()))
    return "\n".join(lines)


_KEY_ALIASES = {
    "ENTER": b"\r",
    "RETURN": b"\r",
    "TAB": b"\t",
    "BACKSPACE": b"\x7f",
    "ESC": b"\x1b",
    "UP": b"\x1b[A",
    "DOWN": b"\x1b[B",
    "LEFT": b"\x1b[D",
    "RIGHT": b"\x1b[C",
    "C-C": b"\x03",
    "C-D": b"\x04",
    "C-Z": b"\x1a",
}
_DECLARED_EXEC_RISK_TIERS: dict[str, ActionRiskTier] = {
    MODEL_EXEC_RUN: "approve",
    MODEL_EXEC_POLL: "silent",
    MODEL_EXEC_SEND_KEYS: "approve",
    MODEL_EXEC_SUBMIT: "approve",
    MODEL_EXEC_PASTE: "approve",
    MODEL_EXEC_KILL: "approve",
    MODEL_EXEC_CLEAR: "approve",
    MODEL_EXEC_LIST: "silent",
}

_CANONICAL_EXECUTABLE_ALIASES: dict[str, str] = {
    "python3": "python3.11",
}


_UNSUPPORTED_REDIRECTION_HINT_TOOL = "file.list_dir"
_UNSUPPORTED_REDIRECTION_HINT_FIX = (
    "Redirections are not supported. For workspace inspection, use "
    "file.list_dir and file.read instead of shell chains. For command output, "
    "run the command directly; stdout and stderr previews are captured "
    "separately."
)
_UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_TOOL = "exec.run"
_UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_FIX = (
    "Redirections, pipes, and shell output truncation are not supported. Run the "
    "verification command directly; stdout and stderr previews are captured "
    "separately."
)
_PYTEST_EXECUTABLE_HINT_TOOL = "exec.run"
_PYTEST_EXECUTABLE_HINT_FIX = (
    "Bare `pytest` is not allowlisted. Run pytest through the allowed Python "
    "module form instead: `python -m pytest -q tests`. Do not use pipes, "
    "redirections, shell chaining, or output truncation."
)
_PACKAGE_INSTALL_HINT_TOOL = "exec.run"
_PACKAGE_INSTALL_HINT_FIX = (
    "Package-manager install commands are not allowlisted for this execution "
    "surface. Do not install the project just to verify local changes. If the "
    "task requires Python test verification, run the allowed direct command "
    "`python -m pytest -q tests` from the workspace instead."
)
_DISCOVERY_HINT_TOOL = "exec.run"
_DISCOVERY_HINT_FIX = (
    "Run toolchain discovery as a direct command such as "
    "`command -v nasm`, then run a separate direct version check such as "
    "`nasm --version` if the tool exists. Do not use pipes, redirections, "
    "or shell chaining."
)


def _artifact_ref_from_runtime(artifact: Any) -> Any:
    ref = preferred_artifact_ref(artifact)
    path = str(getattr(artifact, "path", ""))
    return {
        "ref": ref,
        "kind": "file",
        "name": Path(path).name,
        "meta": {
            "mime": getattr(artifact, "mime", ""),
            "bytes": int(getattr(artifact, "bytes", 0)),
            "sha256": str(getattr(artifact, "sha256", "")),
            "canonical_ref": str(getattr(artifact, "canonical_ref", "") or ""),
        },
    }


def _split_lines_tail(text: str, tail_lines: int) -> str:
    if tail_lines <= 0:
        return ""
    lines = text.splitlines()
    if len(lines) <= tail_lines:
        return text
    return "\n".join(lines[-tail_lines:])


def _decode_preview(payload: bytes, *, tail_lines: Optional[int] = None) -> str:
    decoded = payload.decode("utf-8", errors="replace")
    if tail_lines is not None:
        decoded = _split_lines_tail(decoded, tail_lines)
    if len(decoded) <= EXEC_MAX_PREVIEW_CHARS:
        return decoded
    return decoded[-EXEC_MAX_PREVIEW_CHARS:]


def _status_for_entry(entry: Any) -> str:
    if entry.exit_code is None:
        return "running"
    if bool(entry.killed) or bool(entry.timed_out) or int(entry.exit_code) < 0:
        return "killed"
    return "exited"


def _build_error(
    *,
    code: str,
    message: str,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return ExecErrorModel(
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    ).model_dump()


def _toolchain_discovery_failure_details(command: str) -> Dict[str, str]:
    try:
        parsed = parse_command(command, shell_family=resolve_shell_family())
    except CommandParseError:
        return {}
    if len(parsed.segments) != 1:
        return {}
    argv = list(parsed.segments[0].argv)
    if len(argv) == 3 and argv[:2] == ["command", "-v"]:
        tool = str(argv[2]).strip()
    elif len(argv) == 2 and argv[0] == "which":
        tool = str(argv[1]).strip()
    else:
        return {}
    if not tool:
        return {}
    return {
        "action_class": "toolchain_discovery",
        "discovery_status": "not_found",
        "discovery_tool": tool,
        "next_action": (
            "Treat this as the tool being absent on this host. Do not repeat "
            "the identical discovery command; answer with the absence or ask "
            "whether to install/configure the toolchain."
        ),
    }


def _artifactize_output(
    ctx: RuntimeContext,
    *,
    session_id: str,
    stream: str,
    payload: bytes,
) -> Any:
    if not payload:
        return None
    relative_path = (
        f"artifacts/exec/{_slug(session_id)}-{stream}-{_timestamp_token()}.log"
    )
    artifact = ctx.write_artifact(relative_path, payload, "text/plain", durable=True)
    return _artifact_ref_from_runtime(artifact)


def _metrics(duration_ms: int, stdout_bytes: bytes, stderr_bytes: bytes) -> Any:
    return ExecMetricsModel(
        duration_ms=max(0, int(duration_ms)),
        bytes_out=len(stdout_bytes),
        bytes_err=len(stderr_bytes),
        retries=0,
    ).model_dump()


def _exec_run_result_from_sandbox(
    *,
    ctx: RuntimeContext,
    request_payload: Mapping[str, Any],
    started: float,
    tool_name: str,
    timeout_s: int,
    exec_result: SandboxExecResult,
) -> Dict[str, Any]:
    stdout_bytes = exec_result.stdout.encode("utf-8", errors="replace")
    stderr_bytes = exec_result.stderr.encode("utf-8", errors="replace")

    stdout_artifact = None
    stderr_artifact = None
    stdout_preview: Optional[str] = None
    stderr_preview: Optional[str] = None
    artifact_session_id = f"sandbox-{uuid.uuid4().hex[:12]}"

    if len(stdout_bytes) > _ARTIFACT_THRESHOLD_BYTES:
        stdout_artifact = _artifactize_output(
            ctx,
            session_id=artifact_session_id,
            stream="stdout",
            payload=stdout_bytes,
        )
        stdout_preview = _decode_preview(stdout_bytes, tail_lines=80)
    elif stdout_bytes:
        stdout_preview = _decode_preview(stdout_bytes)

    if len(stderr_bytes) > _ARTIFACT_THRESHOLD_BYTES:
        stderr_artifact = _artifactize_output(
            ctx,
            session_id=artifact_session_id,
            stream="stderr",
            payload=stderr_bytes,
        )
        stderr_preview = _decode_preview(stderr_bytes, tail_lines=80)
    elif stderr_bytes:
        stderr_preview = _decode_preview(stderr_bytes)

    status = "ok"
    summary = _command_summary(
        exit_code=exec_result.returncode,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
    )
    error_payload = None
    if exec_result.timed_out:
        status = "timeout"
        summary = f"Command timed out after {timeout_s}s."
        error_payload = _build_error(
            code="SANDBOX_RESOURCE_LIMIT",
            message="sandbox resource limit exceeded",
            details={"timeout_s": timeout_s},
        )
    elif int(exec_result.returncode or 0) != 0:
        details: Dict[str, Any] = {"exit_code": exec_result.returncode}
        details.update(
            _toolchain_discovery_failure_details(
                str(request_payload.get("command", "") or "")
            )
        )
        message = f"command exited with code {exec_result.returncode}"
        if details.get("discovery_status") == "not_found":
            tool = str(details.get("discovery_tool", "tool") or "tool")
            summary = f"Toolchain discovery did not find {tool}."
            status = "ok"
        else:
            status = "error"
            error_payload = _build_error(
                code="EXEC_ERROR",
                message=message,
                details=details,
            )

    result = ExecRunResult(
        status=status,  # type: ignore[arg-type]
        exit_code=exec_result.returncode,
        summary=summary,
        stdout_artifact=stdout_artifact,
        stderr_artifact=stderr_artifact,
        stdout=stdout_preview,
        stderr=stderr_preview,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        metrics=_metrics(
            int((time.monotonic() - started) * 1000), stdout_bytes, stderr_bytes
        ),
        error=ExecErrorModel.model_validate(error_payload) if error_payload else None,
    )

    if status == "ok":
        emit_family_event(
            ctx,
            event="tool.completed",
            payload={
                "request": dict(request_payload),
                "exit_code": exec_result.returncode,
                "sandbox_runner": True,
            },
        )
        _emit_exec_operation(
            ctx,
            operation="run",
            tool_name=tool_name,
            status="ok",
            extra={"exit_code": exec_result.returncode, "sandbox_runner": True},
        )
    else:
        emit_family_event(
            ctx,
            event="tool.failed",
            payload={
                "request": dict(request_payload),
                "exit_code": exec_result.returncode,
                "sandbox_runner": True,
                "error": error_payload,
            },
        )
        error_code = None
        if isinstance(error_payload, dict):
            error_code = str(error_payload.get("code", "")).strip().upper() or None
        _emit_exec_operation(
            ctx,
            operation="run",
            tool_name=tool_name,
            status="error",
            error_code=error_code,
            extra={"exit_code": exec_result.returncode, "sandbox_runner": True},
        )
        if status == "timeout":
            _emit_exec_operation(
                ctx,
                operation="timeout",
                tool_name=tool_name,
                status="error",
                error_code="TIMEOUT",
                extra={"timeout_s": timeout_s, "sandbox_runner": True},
            )

    return result.model_dump()


def _sandbox_error_result(
    *,
    ctx: RuntimeContext,
    request_payload: Mapping[str, Any],
    started: float,
    tool_name: str,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = ExecRunResult(
        status="error",
        summary=message,
        metrics=ExecMetricsModel(duration_ms=int((time.monotonic() - started) * 1000)),
        error=ExecErrorModel(
            code=code,
            message=message,
            details=details or {},
        ),
    )
    emit_family_event(
        ctx,
        event="tool.failed",
        payload={
            "request": dict(request_payload),
            "error": result.error.model_dump(),
            "sandbox_runner": True,
        },
    )
    _emit_exec_operation(
        ctx,
        operation="run",
        tool_name=tool_name,
        status="error",
        error_code=code,
        extra={"sandbox_runner": True},
    )
    return result.model_dump()


def _duration_ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _exec_run_error_result(
    *,
    ctx: RuntimeContext,
    request_payload: dict[str, Any],
    started: float,
    tool_name: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    summary: str | None = None,
    status: str = "error",
    stdout_bytes: bytes = b"",
    stderr_bytes: bytes = b"",
) -> dict[str, Any]:
    result = ExecRunResult(
        status=status,  # type: ignore[arg-type]
        summary=summary or message,
        metrics=_metrics(_duration_ms_since(started), stdout_bytes, stderr_bytes),
        error=ExecErrorModel(code=code, message=message, details=details or {}),
    )
    emit_family_event(
        ctx,
        event="tool.failed",
        payload={"request": request_payload, "error": result.error.model_dump()},
    )
    _emit_exec_operation(
        ctx,
        operation="run",
        tool_name=tool_name,
        status="error",
        error_code=code,
    )
    return result.model_dump()


def _exec_run_approval_pending_result(
    *,
    ctx: RuntimeContext,
    started: float,
    tool_name: str,
    approval_id: str,
) -> dict[str, Any]:
    approval = pending_approval_decision(
        declared_risk_tier=_declared_exec_risk_tier(tool_name),
        reason="host_execution_requires_approval",
    )
    result = ExecRunResult(
        status="approval-pending",
        risk_tier=approval.risk_tier,
        approval_id=approval_id,
        approval_response=approval.response,
        summary="Host execution requires approval.",
        metrics=ExecMetricsModel(duration_ms=_duration_ms_since(started)),
    )
    _emit_exec_operation(
        ctx,
        operation="run",
        tool_name=tool_name,
        status="ok",
        extra={"status": result.status, "approval_id": approval_id},
    )
    return result.model_dump()


def _build_completed_exec_run_result(
    *,
    ctx: RuntimeContext,
    request_payload: dict[str, Any],
    started: float,
    tool_name: str,
    session_id: str,
    snapshot: Any,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
) -> dict[str, Any]:
    stdout_artifact = None
    stderr_artifact = None
    stdout_preview: Optional[str] = None
    stderr_preview: Optional[str] = None

    if len(stdout_bytes) > _ARTIFACT_THRESHOLD_BYTES:
        stdout_artifact = _artifactize_output(
            ctx, session_id=session_id, stream="stdout", payload=stdout_bytes
        )
        stdout_preview = _decode_preview(stdout_bytes, tail_lines=80)
    elif stdout_bytes:
        stdout_preview = _decode_preview(stdout_bytes)

    if len(stderr_bytes) > _ARTIFACT_THRESHOLD_BYTES:
        stderr_artifact = _artifactize_output(
            ctx, session_id=session_id, stream="stderr", payload=stderr_bytes
        )
        stderr_preview = _decode_preview(stderr_bytes, tail_lines=80)
    elif stderr_bytes:
        stderr_preview = _decode_preview(stderr_bytes)

    status = "ok"
    summary = _command_summary(
        exit_code=snapshot.exit_code,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
    )
    error_payload = None
    if bool(snapshot.timed_out):
        status = "timeout"
        summary = f"Command timed out after {snapshot.timeout_s}s."
        error_payload = _build_error(
            code="TIMEOUT",
            message="command timed out",
            details={"timeout_s": snapshot.timeout_s},
        )
    elif int(snapshot.exit_code or 0) != 0:
        details: Dict[str, Any] = {"exit_code": snapshot.exit_code}
        details.update(
            _toolchain_discovery_failure_details(
                str(request_payload.get("command", "") or "")
            )
        )
        message = f"command exited with code {snapshot.exit_code}"
        if details.get("discovery_status") == "not_found":
            tool = str(details.get("discovery_tool", "tool") or "tool")
            summary = f"Toolchain discovery did not find {tool}."
            status = "ok"
        else:
            status = "error"
            error_payload = _build_error(
                code="EXEC_ERROR",
                message=message,
                details=details,
            )

    result = ExecRunResult(
        status=status,  # type: ignore[arg-type]
        exit_code=snapshot.exit_code,
        summary=summary,
        stdout_artifact=stdout_artifact,
        stderr_artifact=stderr_artifact,
        stdout=stdout_preview,
        stderr=stderr_preview,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        metrics=_metrics(_duration_ms_since(started), stdout_bytes, stderr_bytes),
        error=ExecErrorModel.model_validate(error_payload) if error_payload else None,
    )
    if status == "ok":
        emit_family_event(
            ctx,
            event="tool.completed",
            payload={
                "request": request_payload,
                "exit_code": snapshot.exit_code,
                "session_id": session_id,
            },
        )
        _emit_exec_operation(
            ctx,
            operation="run",
            tool_name=tool_name,
            status="ok",
            extra={"session_id": session_id, "exit_code": snapshot.exit_code},
        )
        return result.model_dump()

    emit_family_event(
        ctx,
        event="tool.failed",
        payload={
            "request": request_payload,
            "exit_code": snapshot.exit_code,
            "session_id": session_id,
            "error": error_payload,
        },
    )
    error_code = None
    if isinstance(error_payload, dict):
        error_code = str(error_payload.get("code", "")).strip().upper() or None
    _emit_exec_operation(
        ctx,
        operation="run",
        tool_name=tool_name,
        status="error",
        error_code=error_code,
        extra={"session_id": session_id, "exit_code": snapshot.exit_code},
    )
    if status == "timeout":
        _emit_exec_operation(
            ctx,
            operation="timeout",
            tool_name=tool_name,
            status="error",
            error_code="TIMEOUT",
            extra={"session_id": session_id, "timeout_s": snapshot.timeout_s},
        )
    return result.model_dump()
