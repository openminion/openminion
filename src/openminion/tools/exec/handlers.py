import re
import time
from typing import Any, Dict, Iterable

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
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
)

from .constants import (
    EXEC_APPROVAL_PENDING_STATUSES,
    EXEC_ARTIFACT_THRESHOLD_BYTES,
)
from .process import PROCESS_MANAGER
from .schemas import (
    ExecErrorModel,
    ExecRunArgs,
    ProcessAckResult,
    ProcessClearArgs,
    ProcessKillArgs,
    ProcessListArgs,
    ProcessListResult,
    ProcessPollArgs,
    ProcessPollResult,
    ProcessPasteArgs,
    ProcessSendKeysArgs,
    ProcessSubmitArgs,
)

from .events import _emit_exec_operation
from .policy import _agent_id, _sanitize_command
from .results import _artifactize_output, _decode_preview, _status_for_entry
from .sessions import (
    _prepare_exec_run,
    _sandbox_session_manager_for_ctx,
    _session_backend_for_ctx,
    _start_exec_run_session,
)


_ARTIFACT_THRESHOLD_BYTES = EXEC_ARTIFACT_THRESHOLD_BYTES
_APPROVAL_PENDING_STATUSES = EXEC_APPROVAL_PENDING_STATUSES


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
_PYTHON_DISCOVERY_HINT_TOOL = "exec.run"
_PYTHON_DISCOVERY_HINT_FIX = (
    "Interpreter-discovery commands such as `which python3` are not allowlisted "
    "for this execution surface. If the task requires Python test verification, "
    "run the allowed direct command `python -m pytest -q tests` from the "
    "workspace instead. Do not probe interpreter paths or versions first."
)


def _encode_keys(keys: Iterable[str]) -> bytes:
    output = bytearray()
    for raw in keys:
        normalized = str(raw).strip()
        if not normalized:
            continue
        key_upper = normalized.upper()
        if key_upper in _KEY_ALIASES:
            output.extend(_KEY_ALIASES[key_upper])
            continue
        if re.fullmatch(r"C-[A-Z]", key_upper):
            ctrl_char = ord(key_upper[-1]) - ord("A") + 1
            output.append(ctrl_char)
            continue
        if len(normalized) == 1:
            output.extend(normalized.encode("utf-8", errors="replace"))
            continue
        # Unknown symbolic key; emit nothing to avoid sending surprising bytes.
    return bytes(output)


def _h_exec_run(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ExecRunArgs.model_validate(args)
    started = time.monotonic()
    tool_name = MODEL_EXEC_RUN
    request_payload = {
        "tool": tool_name,
        "command": _sanitize_command(params.command),
        "workdir": params.workdir,
        "host": params.host,
        "security": params.security,
        "ask": params.ask,
        "ask_fallback": params.ask_fallback,
        "background": params.background,
        "pty": params.pty,
        "timeout_s": params.timeout_s,
        "yield_ms": params.yield_ms,
    }
    emit_family_event(ctx, event="tool.requested", payload={"request": request_payload})
    prep, early_result = _prepare_exec_run(
        params=params,
        ctx=ctx,
        started=started,
        tool_name=tool_name,
        request_payload=request_payload,
    )
    if early_result is not None:
        return early_result
    assert prep is not None
    return _start_exec_run_session(
        params=params,
        prep=prep,
        ctx=ctx,
        started=started,
        tool_name=tool_name,
        request_payload=request_payload,
    )


def _h_process_poll(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessPollArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    session_backend = _session_backend_for_ctx(ctx, params.session_id)
    snapshot = session_backend.snapshot(session_id=params.session_id, agent_id=agent_id)
    if snapshot is None:
        _emit_exec_operation(
            ctx,
            operation="poll",
            tool_name=MODEL_EXEC_POLL,
            status="error",
            error_code="NOT_FOUND",
            extra={"session_id": params.session_id},
        )
        result = ProcessPollResult(
            status="killed",
            summary="Session not found.",
            error=ExecErrorModel(code="NOT_FOUND", message="session not found"),
        )
        return result.model_dump()

    stdout_new, stderr_new = session_backend.consume_new_output(
        session_id=params.session_id, agent_id=agent_id
    )
    stdout_artifact = _artifactize_output(
        ctx, session_id=params.session_id, stream="stdout-chunk", payload=stdout_new
    )
    stderr_artifact = _artifactize_output(
        ctx, session_id=params.session_id, stream="stderr-chunk", payload=stderr_new
    )

    result = ProcessPollResult(
        status=_status_for_entry(snapshot),  # type: ignore[arg-type]
        exit_code=snapshot.exit_code,
        new_stdout_artifact=stdout_artifact,
        new_stderr_artifact=stderr_artifact,
        stdout_preview=_decode_preview(stdout_new, tail_lines=params.tail_lines)
        if stdout_new
        else None,
        stderr_preview=_decode_preview(stderr_new, tail_lines=params.tail_lines)
        if stderr_new
        else None,
        summary=f"Session {params.session_id} status={_status_for_entry(snapshot)}",
    )
    _emit_exec_operation(
        ctx,
        operation="poll",
        tool_name=MODEL_EXEC_POLL,
        status="ok",
        extra={"session_id": params.session_id, "status": result.status},
    )
    return result.model_dump()


def _h_process_send_keys(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessSendKeysArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    session_backend = _session_backend_for_ctx(ctx, params.session_id)
    snapshot = session_backend.snapshot(session_id=params.session_id, agent_id=agent_id)
    if snapshot is None:
        return ProcessAckResult(
            status="error",
            summary="Session not found.",
            session_id=params.session_id,
            error=ExecErrorModel(code="NOT_FOUND", message="session not found"),
        ).model_dump()
    if not bool(snapshot.use_pty):
        return ProcessAckResult(
            status="error",
            summary="send_keys requires a PTY session.",
            session_id=params.session_id,
            error=ExecErrorModel(
                code="INVALID_REQUEST", message="send_keys requires pty=true"
            ),
        ).model_dump()

    payload = _encode_keys(params.keys)
    if not payload:
        return ProcessAckResult(
            status="error",
            summary="No encodable keys were provided.",
            session_id=params.session_id,
            error=ExecErrorModel(
                code="INVALID_ARGUMENT",
                message="keys list did not contain supported keys",
            ),
        ).model_dump()

    ok, message = session_backend.send_input(
        session_id=params.session_id, agent_id=agent_id, payload=payload
    )
    return ProcessAckResult(
        status="ok" if ok else "error",
        summary=message,
        session_id=params.session_id,
        error=None if ok else ExecErrorModel(code="EXEC_ERROR", message=message),
    ).model_dump()


def _h_process_submit(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessSubmitArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    session_backend = _session_backend_for_ctx(ctx, params.session_id)
    ok, message = session_backend.send_input(
        session_id=params.session_id, agent_id=agent_id, payload=b"\r"
    )
    return ProcessAckResult(
        status="ok" if ok else "error",
        summary=message,
        session_id=params.session_id,
        error=None if ok else ExecErrorModel(code="EXEC_ERROR", message=message),
    ).model_dump()


def _h_process_paste(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessPasteArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    payload = params.text.encode("utf-8", errors="replace")
    if params.bracketed:
        payload = b"\x1b[200~" + payload + b"\x1b[201~"
    session_backend = _session_backend_for_ctx(ctx, params.session_id)
    ok, message = session_backend.send_input(
        session_id=params.session_id, agent_id=agent_id, payload=payload
    )
    return ProcessAckResult(
        status="ok" if ok else "error",
        summary=message,
        session_id=params.session_id,
        error=None if ok else ExecErrorModel(code="EXEC_ERROR", message=message),
    ).model_dump()


def _h_process_kill(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessKillArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    session_backend = _session_backend_for_ctx(ctx, params.session_id)
    ok, message, snapshot = session_backend.kill(
        session_id=params.session_id,
        agent_id=agent_id,
        signal_name=params.signal,
    )
    status = "ok" if ok else "error"
    details = {"status": _status_for_entry(snapshot)} if snapshot is not None else {}
    requested_signal = str(params.signal or "TERM").strip().upper() or "TERM"
    operation = "kill" if requested_signal in {"KILL", "SIGKILL"} else "stop"
    _emit_exec_operation(
        ctx,
        operation=operation,
        tool_name=MODEL_EXEC_KILL,
        status=status,
        error_code=None if ok else "NOT_FOUND",
        extra={"session_id": params.session_id, "signal": requested_signal, **details},
    )
    return ProcessAckResult(
        status=status,  # type: ignore[arg-type]
        summary=message,
        session_id=params.session_id,
        error=None
        if ok
        else ExecErrorModel(code="NOT_FOUND", message=message, details=details),
    ).model_dump()


def _h_process_clear(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessClearArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    session_backend = _session_backend_for_ctx(ctx, params.session_id)
    ok, message = session_backend.clear(session_id=params.session_id, agent_id=agent_id)
    return ProcessAckResult(
        status="ok" if ok else "error",
        summary=message,
        session_id=params.session_id,
        error=None if ok else ExecErrorModel(code="INVALID_REQUEST", message=message),
    ).model_dump()


def _h_process_list(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    params = ProcessListArgs.model_validate(args)
    agent_id = _agent_id(ctx)
    sessions = PROCESS_MANAGER.list(
        agent_id=agent_id, include_exited=params.include_exited
    )
    sandbox_sessions = _sandbox_session_manager_for_ctx(ctx)
    if sandbox_sessions is not None:
        sessions.extend(
            sandbox_sessions.list(
                agent_id=agent_id,
                include_exited=params.include_exited,
            )
        )
        sessions.sort(
            key=lambda row: float(row.get("started_at_unix", 0.0)),
            reverse=True,
        )
    return ProcessListResult(sessions=sessions).model_dump()
