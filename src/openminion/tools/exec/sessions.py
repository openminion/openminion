import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from openminion.base.runtime.sandbox import (
    ExecSpec,
    ExecutionSandboxSpec,
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
from openminion.modules.tool.commands import normalize_cd_prefixed_command
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
)

from .command_parser import (
    is_read_only_exec_command,
)
from .constants import (
    EXEC_APPROVAL_PENDING_STATUSES,
    EXEC_ARTIFACT_THRESHOLD_BYTES,
)
from .process import PROCESS_MANAGER, ShellFamily, _select_shell
from openminion.modules.runtime.sandboxes.daytona import DaytonaClientError
from .schemas import (
    ExecRunArgs,
    ExecRunResult,
)

from .events import _emit_exec_operation
from .policy import (
    _agent_id,
    _env_overrides_for_mode,
    _host_execution_enabled,
    _normalize_executable_variants,
    _sanitize_command,
    _validate_command_against_policy,
    _validate_host_allowlist,
)
from .results import (
    _build_completed_exec_run_result,
    _duration_ms_since,
    _exec_run_approval_pending_result,
    _exec_run_error_result,
    _exec_run_result_from_sandbox,
    _metrics,
    _sandbox_error_result,
)
from .workspace import (
    _normalize_capture_redirection_suffix,
    _resolve_workspace_cwd,
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
_DISCOVERY_HINT_TOOL = "exec.run"
_DISCOVERY_HINT_FIX = (
    "Run toolchain discovery as a direct command such as "
    "`command -v nasm`, then run a separate direct version check such as "
    "`nasm --version` if the tool exists. Do not use pipes, redirections, "
    "or shell chaining."
)


@dataclass(frozen=True)
class _ExecRunPreparation:
    agent_id: str
    params: ExecRunArgs
    cwd_path: Path
    env: dict[str, str]
    shell_argv: list[str]
    shell_family: ShellFamily
    sandbox_spec: ExecutionSandboxSpec | None
    exec_spec: ExecSpec | None
    session_backend: Any
    use_sandbox_runner: bool
    use_sandbox_sessions: bool
    sandbox_runner: Any | None


def _sandbox_runner_for_ctx(ctx: RuntimeContext) -> Any | None:
    return getattr(ctx, "sandbox_runner", None)


def _sandbox_session_manager_for_ctx(ctx: RuntimeContext) -> Any | None:
    runner = _sandbox_runner_for_ctx(ctx)
    manager = getattr(runner, "session_manager", None)
    return manager


def _session_backend_for_ctx(ctx: RuntimeContext, session_id: str) -> Any:
    sandbox_sessions = _sandbox_session_manager_for_ctx(ctx)
    if sandbox_sessions is not None and bool(
        getattr(sandbox_sessions, "owns_session_id", lambda value: False)(session_id)
    ):
        return sandbox_sessions
    return PROCESS_MANAGER


def _sandbox_env_allowlist(ctx: RuntimeContext) -> list[str]:
    env_cfg = (getattr(ctx.policy, "raw", {}) or {}).get("env", {})
    if not isinstance(env_cfg, Mapping):
        return []
    return [
        str(name).strip()
        for name in list(env_cfg.get("allow_keys", []) or [])
        if str(name).strip()
    ]


def _build_exec_sandbox_spec(
    *,
    ctx: RuntimeContext,
    cwd_path: Path,
    shell_argv: list[str],
    timeout_s: int,
) -> ExecutionSandboxSpec:
    shell_cmd = str(shell_argv[0] if shell_argv else "").strip()
    cmd_allowlist: list[str] = []
    if shell_cmd:
        cmd_allowlist.append(shell_cmd)
        shell_name = Path(shell_cmd).name
        if shell_name and shell_name not in cmd_allowlist:
            cmd_allowlist.append(shell_name)
    workspace_root = str(Path(ctx.workspace).expanduser().resolve(strict=False))
    return ExecutionSandboxSpec(
        workspace_root=workspace_root,
        read_allow=[workspace_root],
        write_allow=[workspace_root],
        delete_allow=[],
        cmd_allowlist=cmd_allowlist,
        env_allowlist=_sandbox_env_allowlist(ctx),
        timeout_s=float(timeout_s),
        max_output_bytes=EXEC_ARTIFACT_THRESHOLD_BYTES * 4,
        session_mode="foreground",
    )


def _prepare_exec_run(
    *,
    params: ExecRunArgs,
    ctx: RuntimeContext,
    started: float,
    tool_name: str,
    request_payload: dict[str, Any],
) -> tuple[_ExecRunPreparation | None, dict[str, Any] | None]:
    normalized_command, normalized_workdir = normalize_cd_prefixed_command(
        command=params.command,
        workdir=params.workdir,
    )
    normalized_command = _normalize_capture_redirection_suffix(normalized_command)
    normalized_command = _normalize_executable_variants(normalized_command)
    if normalized_command != params.command or normalized_workdir != params.workdir:
        params = params.model_copy(
            update={
                "command": normalized_command,
                "workdir": normalized_workdir,
            }
        )
        request_payload["command"] = _sanitize_command(params.command)
        request_payload["workdir"] = params.workdir
    agent_id = _agent_id(ctx)
    ok_env, env_error = _env_overrides_for_mode(host=params.host, env=params.env)
    if not ok_env:
        return None, _exec_run_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="POLICY_DENIED",
            message=env_error,
            details={"host": params.host},
            summary=env_error,
            status="denied",
        )

    context_metadata = (getattr(ctx.policy, "raw", {}) or {}).get("context_metadata")
    if (
        isinstance(context_metadata, Mapping)
        and str(context_metadata.get("watch_job", "") or "").strip().lower() == "true"
        and not is_read_only_exec_command(params.command)
    ):
        return None, _exec_run_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="POLICY_DENIED",
            message="watch turns only allow read-only exec.run commands",
            details={
                "watch_job": True,
                "command": _sanitize_command(params.command),
            },
            summary="Watch turns only allow read-only exec.run commands.",
            status="denied",
        )

    if params.host != "sandbox":
        if params.security == "deny":
            return None, _exec_run_error_result(
                ctx=ctx,
                request_payload=request_payload,
                started=started,
                tool_name=tool_name,
                code="POLICY_DENIED",
                message="host execution denied by security mode",
                details={"host": params.host, "security": params.security},
                summary="host execution denied by security=deny",
                status="denied",
            )

        if params.security == "allowlist":
            allowed, message, details = _validate_host_allowlist(params.command, ctx)
            if not allowed:
                return None, _exec_run_error_result(
                    ctx=ctx,
                    request_payload=request_payload,
                    started=started,
                    tool_name=tool_name,
                    code="POLICY_DENIED",
                    message=message,
                    details=details,
                    summary=message,
                    status="denied",
                )

        if not _host_execution_enabled(ctx):
            return None, _exec_run_error_result(
                ctx=ctx,
                request_payload=request_payload,
                started=started,
                tool_name=tool_name,
                code="UNSANDBOXED_EXEC_DISABLED",
                message="unsandboxed execution disabled",
                details={"host": params.host},
                summary=(
                    "Unsandboxed execution is disabled "
                    "(set --allow-unsandboxed-exec or "
                    "OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC=1 to enable)."
                ),
                status="denied",
            )

        if params.ask in _APPROVAL_PENDING_STATUSES:
            approval_id = f"approval_{uuid.uuid4().hex[:12]}"
            emit_family_event(
                ctx,
                event="exec.approval_pending",
                payload={"request": request_payload, "approval_id": approval_id},
            )
            return None, _exec_run_approval_pending_result(
                ctx=ctx,
                started=started,
                tool_name=tool_name,
                approval_id=approval_id,
            )

    try:
        cwd_path = _resolve_workspace_cwd(ctx, params.workdir)
    except ValueError as exc:
        return None, _exec_run_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="INVALID_ARGUMENT",
            message=str(exc),
            details={"workdir": params.workdir},
        )

    allowed, message, details = _validate_command_against_policy(params.command, ctx)
    if not allowed:
        return None, _exec_run_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="POLICY_DENIED",
            message=message,
            details=details,
            summary=message,
            status="denied",
        )

    env = ctx.policy.filter_env(dict(params.env))
    sandbox_runner = _sandbox_runner_for_ctx(ctx)
    sandbox_sessions = _sandbox_session_manager_for_ctx(ctx)
    use_sandbox_runner = (
        params.host == "sandbox"
        and not params.background
        and not params.pty
        and sandbox_runner is not None
    )
    use_sandbox_sessions = (
        params.host == "sandbox"
        and (params.background or params.pty)
        and sandbox_sessions is not None
    )
    shell_argv, shell_family = _select_shell(params.command)
    sandbox_spec = None
    exec_spec = None
    if params.host == "sandbox" and sandbox_runner is not None:
        session_mode = "foreground"
        if params.pty:
            session_mode = "pty"
        elif params.background:
            session_mode = "background"
        sandbox_spec = _build_exec_sandbox_spec(
            ctx=ctx,
            cwd_path=cwd_path,
            shell_argv=shell_argv,
            timeout_s=params.timeout_s,
        )
        sandbox_spec = replace(sandbox_spec, session_mode=session_mode)
        exec_spec = ExecSpec(cmd=shell_argv, cwd=str(cwd_path), env=env)

    if (
        params.host == "sandbox"
        and sandbox_runner is not None
        and (params.background or params.pty)
        and not use_sandbox_sessions
    ):
        mode = "background" if params.background else "pty"
        return None, _sandbox_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="SANDBOX_SESSION_UNSUPPORTED",
            message=f"sandbox {mode} sessions are not supported yet",
            details={"host": params.host, "mode": mode},
        )

    session_backend = sandbox_sessions if use_sandbox_sessions else PROCESS_MANAGER
    return (
        _ExecRunPreparation(
            agent_id=agent_id,
            params=params,
            cwd_path=cwd_path,
            env=env,
            shell_argv=shell_argv,
            shell_family=shell_family,
            sandbox_spec=sandbox_spec,
            exec_spec=exec_spec,
            session_backend=session_backend,
            use_sandbox_runner=use_sandbox_runner,
            use_sandbox_sessions=use_sandbox_sessions,
            sandbox_runner=sandbox_runner,
        ),
        None,
    )


def _start_exec_run_session(
    *,
    params: ExecRunArgs,
    prep: _ExecRunPreparation,
    ctx: RuntimeContext,
    started: float,
    tool_name: str,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    if prep.use_sandbox_runner:
        assert prep.sandbox_runner is not None
        try:
            exec_result = prep.sandbox_runner.run_exec(
                prep.exec_spec,
                prep.sandbox_spec,
            )
        except DaytonaClientError as exc:
            return _sandbox_error_result(
                ctx=ctx,
                request_payload=request_payload,
                started=started,
                tool_name=tool_name,
                code=str(exc.code or "SANDBOX_UNAVAILABLE"),
                message=str(exc.message or "sandbox execution failed"),
                details=dict(exc.details or {}),
            )
        except Exception as exc:
            message = f"failed to start command: {type(exc).__name__}: {exc}"
            return _sandbox_error_result(
                ctx=ctx,
                request_payload=request_payload,
                started=started,
                tool_name=tool_name,
                code="SANDBOX_UNAVAILABLE",
                message=message,
                details={"exception_type": type(exc).__name__},
            )

        emit_family_event(
            ctx,
            event="tool.started",
            payload={
                "request": request_payload,
                "agent_id": prep.agent_id,
                "sandbox_runner": True,
            },
        )
        return _exec_run_result_from_sandbox(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            timeout_s=params.timeout_s,
            exec_result=exec_result,
        )

    try:
        prepared_params = prep.params
        if prep.use_sandbox_sessions:
            session = prep.session_backend.start(
                agent_id=prep.agent_id,
                command=prepared_params.command,
                cwd=str(prep.cwd_path),
                env=prep.env,
                use_pty=prepared_params.pty,
                timeout_s=prepared_params.timeout_s,
                host=prepared_params.host,
                shell_family=prep.shell_family.value,
                exec_spec=prep.exec_spec,
                sandbox=prep.sandbox_spec,
            )
        else:
            session = prep.session_backend.start(
                agent_id=prep.agent_id,
                command=prepared_params.command,
                cwd=str(prep.cwd_path),
                env=prep.env,
                use_pty=prepared_params.pty,
                timeout_s=prepared_params.timeout_s,
                host=prepared_params.host,
            )
    except Exception as exc:
        return _exec_run_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="EXEC_ERROR",
            message=f"failed to start command: {type(exc).__name__}: {exc}",
        )

    emit_family_event(
        ctx,
        event="tool.started",
        payload={
            "request": request_payload,
            "session_id": session.session_id,
            "agent_id": prep.agent_id,
        },
    )

    if prepared_params.background:
        running_result = ExecRunResult(
            status="running",
            session_id=session.session_id,
            summary="Command started in background.",
            metrics=_metrics(_duration_ms_since(started), b"", b""),
        )
        _emit_exec_operation(
            ctx,
            operation="run",
            tool_name=tool_name,
            status="ok",
            extra={"status": running_result.status, "session_id": session.session_id},
        )
        return running_result.model_dump()

    wait_seconds = min(
        float(prepared_params.timeout_s),
        max(0.0, float(prepared_params.yield_ms) / 1000.0),
    )
    finished = prep.session_backend.wait(
        session_id=session.session_id,
        agent_id=prep.agent_id,
        wait_seconds=wait_seconds,
    )
    if not finished:
        emit_family_event(
            ctx,
            event="tool.running_notice",
            payload={"request": request_payload, "session_id": session.session_id},
        )
        running_result = ExecRunResult(
            status="running",
            session_id=session.session_id,
            summary="Command still running; use exec.poll with returned session_id.",
            metrics=_metrics(_duration_ms_since(started), b"", b""),
        )
        _emit_exec_operation(
            ctx,
            operation="run",
            tool_name=tool_name,
            status="ok",
            extra={"status": running_result.status, "session_id": session.session_id},
        )
        return running_result.model_dump()

    snapshot = prep.session_backend.snapshot(
        session_id=session.session_id, agent_id=prep.agent_id
    )
    stdout_bytes, stderr_bytes = prep.session_backend.full_output(
        session_id=session.session_id, agent_id=prep.agent_id
    )
    if snapshot is None:
        return _exec_run_error_result(
            ctx=ctx,
            request_payload=request_payload,
            started=started,
            tool_name=tool_name,
            code="NOT_FOUND",
            message="session not found",
            summary="Session disappeared before completion was recorded.",
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
        )

    result = _build_completed_exec_run_result(
        ctx=ctx,
        request_payload=request_payload,
        started=started,
        tool_name=tool_name,
        session_id=session.session_id,
        snapshot=snapshot,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
    )
    prep.session_backend.clear(session_id=session.session_id, agent_id=prep.agent_id)
    return result
