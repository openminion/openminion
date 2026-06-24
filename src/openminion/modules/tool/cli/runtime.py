from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import yaml
from pydantic import ValidationError

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.modules.telemetry.adapter import create_telemetry_adapter
from openminion.tools.config import resolve_tool_env
from ..adapters import AllowAllSafetyAdapter, LocalPolicyAdapter
from ..constants import (
    DEFAULT_TOOL_RUNS_DIRNAME,
    OPENMINION_CONFIG_PATH_ENV,
    OPENMINION_DATA_ROOT_ENV,
)
from .runtime_invocation import (
    finalize_cli_call as finalize_cli_call_invocation,
    invoke_exec_tool as invoke_exec_tool_invocation,
    invoke_pinchtab_tool as invoke_pinchtab_tool_invocation,
    is_unknown_browser_tool_error as is_unknown_browser_tool_error_invocation,
    map_pinchtab_to_browser_call as map_pinchtab_to_browser_call_invocation,
    parse_env_pairs as parse_env_pairs_invocation,
    pinchtab_daemon_config as pinchtab_daemon_config_invocation,
)
from ..errors import ToolRuntimeError
from ..runtime.plugins import load_plugins
from ..runtime.policy import Policy
from ..registry import ToolRegistry, ToolSpec
from ..runtime import (
    RuntimeContext,
    build_runtime_repositories,
    create_run_root,
    iso_now,
    make_error_envelope,
    make_ok_envelope,
    new_run_id,
)
from ..contracts.schemas import (
    CallRequest,
    CmdRunArgs,
    CmdWhichArgs,
    FsCopyMoveArgs,
    FsDeleteArgs,
    FsListDirArgs,
    FsReadFileArgs,
    FsSearchArgs,
    FsWriteFileArgs,
    ProcDetailsArgs,
    ProcKillArgs,
    ProcListArgs,
    ResultEnvelope,
    Scope,
    SysInfoArgs,
)
from ..diagnostics.events import emit_tool_exec_operation_for_context
from ..runtime.tools_core import (
    h_cmd_run,
    h_cmd_which,
    h_fs_copy,
    h_fs_delete,
    h_fs_list_dir,
    h_fs_move,
    h_fs_read_file,
    h_fs_search,
    h_fs_write_file,
    h_proc_details,
    h_proc_kill,
    h_proc_list,
    h_sys_info,
)


def print_obj(obj: Dict[str, Any], json_out: bool = True) -> None:
    if json_out:
        print(json.dumps(obj, indent=2, ensure_ascii=True))
        return
    print(yaml.safe_dump(obj, sort_keys=False))


def parse_call_payload(payload: Optional[str]) -> CallRequest:
    raw = payload if payload is not None else sys.stdin.read()
    if not raw.strip():
        raise ToolRuntimeError("INVALID_ARGUMENT", "Empty call payload")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", f"Invalid JSON payload: {exc}"
        ) from exc
    try:
        return CallRequest.model_validate(parsed)
    except ValidationError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Call request schema validation failed",
            {"errors": exc.errors()},
        ) from exc


def _runtime_env_from_policy(policy: Policy) -> Mapping[str, object] | None:
    raw = getattr(policy, "raw", {}) or {}
    if not isinstance(raw, Mapping):
        return None
    context_meta = raw.get("context_metadata")
    if not isinstance(context_meta, Mapping):
        return None
    runtime_env = context_meta.get("runtime_env")
    if isinstance(runtime_env, Mapping):
        return runtime_env
    if isinstance(runtime_env, str):
        try:
            parsed = json.loads(runtime_env)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, Mapping):
            return parsed
    return None


def _resolve_policy_env(policy: Policy):
    return resolve_tool_env(runtime_env=_runtime_env_from_policy(policy))


_FILE_TOOL_SPECS: tuple[tuple[str, Any, str, Any, bool, bool], ...] = (
    ("list_dir", FsListDirArgs, "READ_ONLY", h_fs_list_dir, False, True),
    ("read_file", FsReadFileArgs, "READ_ONLY", h_fs_read_file, False, True),
    ("write_file", FsWriteFileArgs, "WRITE_SAFE", h_fs_write_file, False, True),
    ("copy", FsCopyMoveArgs, "WRITE_SAFE", h_fs_copy, False, True),
    ("move", FsCopyMoveArgs, "WRITE_SAFE", h_fs_move, False, True),
    ("delete", FsDeleteArgs, "WRITE_SAFE", h_fs_delete, True, False),
    ("search", FsSearchArgs, "READ_ONLY", h_fs_search, False, True),
)


def _register_file_tools(reg: ToolRegistry) -> None:
    for name, args_model, min_scope, handler, dangerous, idempotent in _FILE_TOOL_SPECS:
        reg.add(
            ToolSpec(
                f"file.{name}",
                args_model,
                min_scope,
                handler,
                dangerous=dangerous,
                idempotent=idempotent,
                tags=("core", "file"),
            )
        )


def _register_cmd_tools(reg: ToolRegistry) -> None:
    reg.add(
        ToolSpec(
            "cmd.run",
            CmdRunArgs,
            "WRITE_SAFE",
            h_cmd_run,
            idempotent=False,
            tags=("core", "cmd"),
        )
    )
    reg.add(
        ToolSpec(
            "cmd.which",
            CmdWhichArgs,
            "READ_ONLY",
            h_cmd_which,
            tags=("core", "cmd"),
        )
    )


def _register_proc_tools(reg: ToolRegistry) -> None:
    reg.add(
        ToolSpec(
            "proc.list",
            ProcListArgs,
            "READ_ONLY",
            h_proc_list,
            tags=("core", "proc"),
        )
    )
    reg.add(
        ToolSpec(
            "proc.details",
            ProcDetailsArgs,
            "READ_ONLY",
            h_proc_details,
            tags=("core", "proc"),
        )
    )
    reg.add(
        ToolSpec(
            "proc.kill",
            ProcKillArgs,
            "POWER_USER",
            h_proc_kill,
            dangerous=True,
            idempotent=False,
            tags=("core", "proc"),
        )
    )


def build_registry(policy: Policy) -> tuple[ToolRegistry, list[Dict[str, Any]]]:
    reg = ToolRegistry()
    _register_file_tools(reg)
    _register_cmd_tools(reg)
    _register_proc_tools(reg)
    reg.add(
        ToolSpec("sys.info", SysInfoArgs, "READ_ONLY", h_sys_info, tags=("core", "sys"))
    )
    plugin_statuses = load_plugins(reg, policy)
    return reg, plugin_statuses


def effective_scope(policy: Policy, scope: Optional[str]) -> Scope:
    return policy.effective_scope(scope)


def write_run_meta(
    run_root: Path,
    request: CallRequest,
    effective_scope_value: Scope,
    policy_path: Path,
    plugin_statuses: list[Dict[str, Any]],
) -> None:
    args_blob = json.dumps(request.args, sort_keys=True, ensure_ascii=True).encode(
        "utf-8"
    )
    policy_blob = policy_path.read_bytes() if policy_path.exists() else b""
    meta = {
        "tool": request.tool,
        "policy_scope": effective_scope_value,
        "args_sha256": hashlib.sha256(args_blob).hexdigest(),
        "policy_sha256": hashlib.sha256(policy_blob).hexdigest(),
        "plugins": plugin_statuses,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def raise_if_denied(
    stage: str, code: str, reason: str, details: Dict[str, Any]
) -> None:
    raise ToolRuntimeError(
        code if code else "POLICY_DENIED",
        reason or f"{stage} denied",
        {"stage": stage, **details},
    )


def print_envelope(env: ResultEnvelope, json_out: bool) -> None:
    payload = env.model_dump_json(indent=2)
    print(payload if json_out else payload)


def _validate_request_args(spec: Any, req: CallRequest) -> dict[str, Any]:
    """Validate request args against the spec, raising INVALID_ARGUMENT on failure."""
    try:
        return spec.args_model.model_validate(req.args).model_dump()
    except ValidationError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Tool args validation failed",
            {"errors": exc.errors()},
        ) from exc


def _resolve_outer_timeout(pol: Policy, timeout_sec: Optional[int]) -> int:
    """Resolve the outer timeout from the explicit option or policy default."""
    configured_outer = pol.limit_int("outer_timeout_sec", 60)
    outer_timeout = int(timeout_sec) if timeout_sec is not None else configured_outer
    if outer_timeout <= 0:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "timeout_sec must be >= 1",
            {"timeout_sec": outer_timeout},
        )
    return outer_timeout


def _apply_cmd_run_timeout(validated_args: dict[str, Any], outer_timeout: int) -> None:
    """For `cmd.run`, fold the outer timeout into the args' `timeout_sec` field."""
    existing = validated_args.get("timeout_sec")
    if existing is None:
        validated_args["timeout_sec"] = outer_timeout
    else:
        validated_args["timeout_sec"] = min(int(existing), outer_timeout)


def _enforce_safety_and_policy(
    *,
    req: CallRequest,
    spec: Any,
    validated_args: dict[str, Any],
    safety_adapter: AllowAllSafetyAdapter,
    policy_adapter: LocalPolicyAdapter,
) -> tuple[Any, Any, dict[str, Any]]:
    """Run safety + policy adapter chain. Returns updated args after any policy
    `modified_args` rewrite. Raises via `raise_if_denied` on denial."""
    safety_decision = safety_adapter.evaluate(tool=req.tool, args=validated_args)
    if not safety_decision.allowed:
        raise_if_denied(
            "safety",
            safety_decision.code,
            safety_decision.reason,
            safety_decision.details,
        )
    policy_decision = policy_adapter.evaluate(
        tool_name=req.tool,
        tool_spec=spec,
        args=validated_args,
    )
    if not policy_decision.allowed:
        raise_if_denied(
            "policy",
            policy_decision.code,
            policy_decision.reason,
            policy_decision.details,
        )
    if policy_decision.modified_args:
        validated_args = policy_decision.modified_args
    return safety_decision, policy_decision, validated_args


def _maybe_autostart_sidecar_for_spec(spec: Any, env_owner: Any) -> None:
    """If spec carries a sidecar, ensure autostart and raise on failure/disabled."""
    if not (isinstance(spec, ToolSpec) and getattr(spec, "sidecar", None)):
        return
    try:
        from openminion.services.lifecycle.sidecars import ensure_sidecar_autostart

        autostart = ensure_sidecar_autostart(
            name=str(spec.sidecar),
            config_path=env_owner.get(OPENMINION_CONFIG_PATH_ENV, "") or None,
            runtime_env=env_owner.snapshot(),
            interactive=bool(sys.stdin.isatty()),
            logger=logging.getLogger("openminion.sidecars"),
        )
        if not autostart.get("enabled", False):
            raise ToolRuntimeError(
                "CONFIRM_REQUIRED",
                f"sidecar '{spec.sidecar}' not enabled",
                {"sidecar": spec.sidecar, "autostart": autostart},
            )
    except ToolRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"sidecar '{spec.sidecar}' autostart failed: {exc}",
            {"sidecar": spec.sidecar},
        ) from exc


def _build_runtime_context(
    *,
    pol: Policy,
    op_workspace: Path,
    run_root: Path,
    effective_scope_value: Scope,
    confirm_flag: bool,
    logs: list,
    artifacts: list,
    safety_adapter: AllowAllSafetyAdapter,
    policy_adapter: LocalPolicyAdapter,
    telemetryctl: Any,
    run_id: str,
    req: CallRequest,
) -> RuntimeContext:
    return RuntimeContext(
        policy=pol,
        workspace=op_workspace,
        run_root=run_root,
        scope=effective_scope_value,
        confirm=confirm_flag,
        repositories=build_runtime_repositories(
            context_metadata=(getattr(pol, "raw", {}) or {}).get("context_metadata"),
        ),
        logs=logs,
        artifacts=artifacts,
        safety_adapter=safety_adapter,
        policy_adapter=policy_adapter,
        telemetryctl=telemetryctl,
        telemetry_session_id=f"toolrun:{run_id}",
        telemetry_turn_id=req.meta.request_id or run_id,
    )


def _audit_check_decisions(
    ctx: RuntimeContext,
    *,
    req: CallRequest,
    safety_decision: Any,
    policy_decision: Any,
) -> None:
    ctx.write_audit_event(
        {
            "event": "request_received",
            "tool": req.tool,
            "request_id": req.meta.request_id,
        }
    )
    ctx.write_audit_event(
        {
            "event": "safety_check_finished",
            "tool": req.tool,
            "allowed": safety_decision.allowed,
            "code": safety_decision.code,
            "reason": safety_decision.reason,
        }
    )
    ctx.write_audit_event(
        {
            "event": "policy_check_finished",
            "tool": req.tool,
            "allowed": policy_decision.allowed,
            "code": policy_decision.code,
            "reason": policy_decision.reason,
        }
    )


def _run_handler_with_timeout(
    *,
    spec: Any,
    validated_args: dict[str, Any],
    ctx: RuntimeContext,
    outer_timeout: int,
    req: CallRequest,
) -> Any:
    """Run the handler in a thread with `outer_timeout` budget. Emits a
    `tool_exec` telemetry event for `exec.*` timeouts before raising."""
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(spec.handler, validated_args, ctx)
            return future.result(timeout=outer_timeout)
    except FuturesTimeoutError as exc:
        if req.tool.startswith("exec.") and ctx is not None:
            emit_tool_exec_operation_for_context(
                ctx=ctx,
                operation="timeout",
                tool_name=req.tool,
                status="error",
                error_code="TIMEOUT",
                extra={
                    "source": "outer_timeout",
                    "outer_timeout_sec": outer_timeout,
                },
            )
        raise ToolRuntimeError(
            "TIMEOUT",
            "Call exceeded outer timeout",
            {"outer_timeout_sec": outer_timeout},
        ) from exc


def _build_error_envelope_from(
    *,
    req: Optional[CallRequest],
    run_id: str,
    effective_scope_value: Scope,
    started_at: str,
    op_workspace: Path,
    artifacts: list,
    logs: list,
    error: ToolRuntimeError,
) -> ResultEnvelope:
    return make_error_envelope(
        tool=req.tool if req else "unknown",
        run_id=run_id,
        request_id=req.meta.request_id if req else None,
        scope=effective_scope_value,
        started_at=started_at,
        workspace=op_workspace,
        artifacts=artifacts,
        logs=logs,
        error=error,
    )


def execute_call_payload(
    *,
    payload: Optional[str],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    build_registry_fn: Callable[
        [Policy], tuple[ToolRegistry, list[Dict[str, Any]]]
    ] = build_registry,
    create_run_root_fn: Callable[..., Path] = create_run_root,
    resolve_home_root_fn: Callable[[], Path] = resolve_home_root,
    resolve_data_root_fn: Callable[..., Path] = resolve_data_root,
) -> tuple[ResultEnvelope, int]:
    pol = Policy.load(policy)
    env_owner = _resolve_policy_env(pol)
    reg, plugin_statuses = build_registry_fn(pol)
    run_id = new_run_id()
    started_at = iso_now()
    op_workspace = (workspace or Path.cwd()).expanduser().resolve(strict=False)
    logs: list = []
    artifacts: list = []
    effective_scope_value: Scope = pol.max_scope()
    req: Optional[CallRequest] = None
    run_root: Optional[Path] = None
    ctx: RuntimeContext | None = None

    try:
        req = parse_call_payload(payload)
        effective_scope_value = effective_scope(pol, scope)
        confirm_flag = confirm or req.meta.confirm
        try:
            spec = reg.get(req.tool)
        except KeyError as exc:
            raise ToolRuntimeError("NOT_FOUND", f"Unknown tool: {req.tool}") from exc
        validated_args = _validate_request_args(spec, req)
        outer_timeout = _resolve_outer_timeout(pol, timeout_sec)
        if req.tool == "cmd.run":
            _apply_cmd_run_timeout(validated_args, outer_timeout)

        safety_adapter = AllowAllSafetyAdapter()
        policy_adapter = LocalPolicyAdapter(
            policy=pol,
            workspace=op_workspace,
            scope=effective_scope_value,
            confirm=confirm_flag,
        )
        safety_decision, policy_decision, validated_args = _enforce_safety_and_policy(
            req=req,
            spec=spec,
            validated_args=validated_args,
            safety_adapter=safety_adapter,
            policy_adapter=policy_adapter,
        )

        if req.meta.dry_run:
            return make_ok_envelope(
                tool=req.tool,
                run_id=run_id,
                request_id=req.meta.request_id,
                scope=effective_scope_value,
                started_at=started_at,
                workspace=op_workspace,
                artifacts=artifacts,
                logs=logs,
                data={"dry_run": True},
            ), 0

        _maybe_autostart_sidecar_for_spec(spec, env_owner)

        home_root = resolve_home_root_fn()
        data_root = resolve_data_root_fn(
            home_root,
            data_root=env_owner.get(OPENMINION_DATA_ROOT_ENV, "") or None,
        )
        try:
            telemetryctl = create_telemetry_adapter(
                home_root=home_root,
                env=env_owner.snapshot(),
            )
        except Exception:
            telemetryctl = None
        run_root = create_run_root_fn(
            pol,
            run_id,
            root_override=data_root / DEFAULT_TOOL_RUNS_DIRNAME,
        )
        ctx = _build_runtime_context(
            pol=pol,
            op_workspace=op_workspace,
            run_root=run_root,
            effective_scope_value=effective_scope_value,
            confirm_flag=confirm_flag,
            logs=logs,
            artifacts=artifacts,
            safety_adapter=safety_adapter,
            policy_adapter=policy_adapter,
            telemetryctl=telemetryctl,
            run_id=run_id,
            req=req,
        )
        _audit_check_decisions(
            ctx,
            req=req,
            safety_decision=safety_decision,
            policy_decision=policy_decision,
        )
        data = _run_handler_with_timeout(
            spec=spec,
            validated_args=validated_args,
            ctx=ctx,
            outer_timeout=outer_timeout,
            req=req,
        )
        env = make_ok_envelope(
            tool=req.tool,
            run_id=run_id,
            request_id=req.meta.request_id,
            scope=effective_scope_value,
            started_at=started_at,
            workspace=op_workspace,
            artifacts=ctx.artifacts,
            logs=ctx.logs,
            data=data if isinstance(data, dict) else {"result": data},
        )
        write_run_meta(run_root, req, effective_scope_value, policy, plugin_statuses)
        ctx.write_audit_event({"event": "call_completed", "tool": req.tool, "ok": True})
        return env, 0
    except ToolRuntimeError as exc:
        return _build_error_envelope_from(
            req=req,
            run_id=run_id,
            effective_scope_value=effective_scope_value,
            started_at=started_at,
            op_workspace=op_workspace,
            artifacts=artifacts,
            logs=logs,
            error=exc,
        ), 1
    except Exception as exc:
        return _build_error_envelope_from(
            req=req,
            run_id=run_id,
            effective_scope_value=effective_scope_value,
            started_at=started_at,
            op_workspace=op_workspace,
            artifacts=artifacts,
            logs=logs,
            error=ToolRuntimeError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"),
        ), 1


def is_unknown_browser_tool_error(env: ResultEnvelope) -> bool:
    return is_unknown_browser_tool_error_invocation(env)


def map_pinchtab_to_browser_call(
    *, tool: str, args: Dict[str, Any]
) -> tuple[str, Dict[str, Any]]:
    return map_pinchtab_to_browser_call_invocation(tool=tool, args=args)


def invoke_pinchtab_tool(
    *,
    tool: str,
    args: Dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    execute_call_payload_fn: Callable[
        ..., tuple[ResultEnvelope, int]
    ] = execute_call_payload,
) -> tuple[ResultEnvelope, int]:
    return invoke_pinchtab_tool_invocation(
        tool=tool,
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
        execute_call_payload_fn=execute_call_payload_fn,
    )


def pinchtab_daemon_config(
    *,
    base_url: Optional[str] = None,
    launch_cmd: Optional[str] = None,
    launch_timeout_s: int = 20,
    launch_env: Optional[str] = None,
) -> Any:
    return pinchtab_daemon_config_invocation(
        base_url=base_url,
        launch_cmd=launch_cmd,
        launch_timeout_s=launch_timeout_s,
        launch_env=launch_env,
    )


def parse_env_pairs(values: list[str]) -> Dict[str, str]:
    return parse_env_pairs_invocation(values)


def invoke_exec_tool(
    *,
    tool: str,
    args: Dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    execute_call_payload_fn: Callable[
        ..., tuple[ResultEnvelope, int]
    ] = execute_call_payload,
) -> tuple[ResultEnvelope, int]:
    return invoke_exec_tool_invocation(
        tool=tool,
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
        execute_call_payload_fn=execute_call_payload_fn,
    )


def finalize_cli_call(
    env: ResultEnvelope,
    exit_code: int,
    json_out: bool,
    print_envelope_fn: Callable[[ResultEnvelope, bool], None] = print_envelope,
) -> None:
    finalize_cli_call_invocation(
        env=env,
        exit_code=exit_code,
        json_out=json_out,
        print_envelope_fn=print_envelope_fn,
    )
