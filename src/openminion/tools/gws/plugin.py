"""Google Workspace tool plugin."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from pydantic import ValidationError

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.tool.contracts.model_ids import (
    MODEL_GWS_AUTH_EXPORT,
    MODEL_GWS_AUTH_LOGIN,
    MODEL_GWS_AUTH_SETUP,
    MODEL_GWS_CALL,
    MODEL_GWS_SCHEMA,
)
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime.context import RuntimeContext

from .runtime import base_request_payload as _base_request_payload
from .runtime import extract_error_payload as _extract_error_payload
from .runtime import gws_redacted_credential_placeholder
from .runtime import parse_json_or_ndjson as _parse_json_or_ndjson
from .runtime import result_for_event as _result_for_event
from .runtime import summarize_data as _summarize_data
from .constants import (
    GWS_DEFAULT_EXECUTABLE,
    GWS_READ_METHOD_HINTS,
    GWS_SECRET_ENV_PREFIX,
    GWS_WRITE_METHODS,
)
from .interfaces import GWS_INTERFACE_VERSION
from .schemas import (
    GWS_AUTH_EXPORT_INPUT_SCHEMA,
    GWS_AUTH_INPUT_SCHEMA,
    GWS_CALL_INPUT_SCHEMA,
    GWS_RESULT_OUTPUT_SCHEMA,
    GWS_SCHEMA_INPUT_SCHEMA,
    GwsAuthArgs,
    GwsAuthExportArgs,
    GwsCallArgs,
    GwsSchemaArgs,
    GwsToolConfig,
    RiskLevel,
)

_TOOL_CALL = MODEL_GWS_CALL
_TOOL_SCHEMA = MODEL_GWS_SCHEMA
_TOOL_AUTH_SETUP = MODEL_GWS_AUTH_SETUP
_TOOL_AUTH_LOGIN = MODEL_GWS_AUTH_LOGIN
_TOOL_AUTH_EXPORT = MODEL_GWS_AUTH_EXPORT

TOOL_DESCRIPTOR: Dict[str, Any] = {
    "name": "gws",
    "title": "Google Workspace CLI Wrapper",
    "description": "Dynamic invoker around the gws CLI with policy-gated write/admin operations.",
    "version": OPENMINION_VERSION,
    "capabilities": ["read", "write", "admin", "google-workspace", "cli"],
    "risk_spec": {
        "risk_level": "medium",
        "side_effects": "google_workspace_api",
        "default_policy": "confirm_for_write_admin",
    },
    "methods": [
        _TOOL_CALL,
        _TOOL_SCHEMA,
        _TOOL_AUTH_SETUP,
        _TOOL_AUTH_LOGIN,
        _TOOL_AUTH_EXPORT,
    ],
}


@dataclass
class _CommandResult:
    exit_code: int
    timed_out: bool
    stdout_for_parse: str
    stdout_parse_truncated: bool
    raw_stdout: Optional[str]
    raw_stderr: str
    stdout_bytes: int
    stderr_bytes: int


def _emit_event(
    ctx: RuntimeContext, *, event_name: str, payload: Dict[str, Any]
) -> None:
    emit_family_event(ctx, event=event_name, payload=payload)


def _tool_config_payload(ctx: RuntimeContext) -> Mapping[str, Any]:
    raw = getattr(ctx.policy, "raw", {})
    if not isinstance(raw, Mapping):
        return {}
    tools_cfg = raw.get("tools", {})
    if not isinstance(tools_cfg, Mapping):
        return {}
    candidate = tools_cfg.get("gws", {})
    if not isinstance(candidate, Mapping):
        return {}
    return candidate


def _resolve_config(ctx: RuntimeContext) -> GwsToolConfig:
    payload = _tool_config_payload(ctx)
    try:
        return GwsToolConfig.model_validate(payload)
    except ValidationError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Invalid tools.gws configuration",
            {"errors": exc.errors()},
        ) from exc


def _resolve_gws_executable(config: GwsToolConfig) -> str:
    configured = (
        str(config.gws_path or GWS_DEFAULT_EXECUTABLE).strip() or GWS_DEFAULT_EXECUTABLE
    )
    if (
        "/" in configured
        or configured.startswith(".")
        or Path(configured).is_absolute()
    ):
        candidate = Path(configured).expanduser().resolve(strict=False)
        if not candidate.exists():
            raise ToolRuntimeError(
                "DEPENDENCY_MISSING",
                "gws executable path does not exist",
                {"gws_path": configured},
            )
        if not candidate.is_file():
            raise ToolRuntimeError(
                "DEPENDENCY_MISSING",
                "gws executable path is not a file",
                {"gws_path": configured},
            )
        return str(candidate)

    resolved = shutil.which(configured)
    if not resolved:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "gws executable not found on PATH",
            {"gws_path": configured},
        )
    return str(Path(resolved).resolve(strict=False))


def _normalize_service(service: str) -> str:
    return str(service or "").strip().lower()


def _resolve_secret_value(
    ref: Optional[str],
    *,
    env: Mapping[str, str],
    allow_path_fallback: bool = False,
) -> str:
    token = str(ref or "").strip()
    if not token:
        return ""

    raw = token
    if raw.startswith("secret:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        return ""

    normalized = re.sub(r"[^A-Za-z0-9_]", "_", raw).strip("_")
    candidates = [raw]
    for value in (
        raw.upper(),
        raw.replace("/", "_"),
        raw.replace("/", "_").upper(),
        normalized,
        normalized.upper(),
        f"{GWS_SECRET_ENV_PREFIX}{normalized.upper()}" if normalized else "",
    ):
        candidate = str(value or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for env_key in candidates:
        resolved = str(env.get(env_key, "")).strip()
        if resolved:
            return resolved

    if allow_path_fallback:
        path_candidate = Path(raw).expanduser()
        if path_candidate.exists():
            return str(path_candidate.resolve(strict=False))
    return ""


def _build_exec_env(
    config: GwsToolConfig,
    ctx: RuntimeContext,
) -> tuple[Dict[str, str], Dict[str, bool]]:
    env = dict(ctx.env.snapshot())

    token_value = str(config.env.token or "").strip()
    if not token_value:
        token_value = _resolve_secret_value(
            config.env.token_secret,
            env=env,
            allow_path_fallback=False,
        )
    if token_value:
        env["GOOGLE_WORKSPACE_CLI_TOKEN"] = token_value

    creds_value = str(config.env.credentials_file or "").strip()
    if not creds_value:
        creds_value = _resolve_secret_value(
            config.env.credentials_file_secret,
            env=env,
            allow_path_fallback=True,
        )
    if creds_value:
        env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = creds_value

    impersonated_user = str(config.env.impersonated_user or "").strip()
    if impersonated_user:
        env["GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER"] = impersonated_user

    return (
        env,
        {
            "token": bool(str(env.get("GOOGLE_WORKSPACE_CLI_TOKEN", "")).strip()),
            "credentials_file": bool(
                str(env.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", "")).strip()
            ),
            "impersonated_user": bool(
                str(env.get("GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER", "")).strip()
            ),
        },
    )


def _classify_risk(args: GwsCallArgs) -> RiskLevel:
    if args.force_risk is not None:
        return args.force_risk

    service = _normalize_service(args.service)
    method = str(args.method or "").strip().lower()

    if service == "admin" or service.startswith("admin") or service == "directory":
        return "admin"
    if method in GWS_WRITE_METHODS:
        return "write"
    if args.json_payload is not None and method not in GWS_READ_METHOD_HINTS:
        return "write"
    return "read"


def _ensure_call_allowed(
    *, args: GwsCallArgs, ctx: RuntimeContext, config: GwsToolConfig, risk: RiskLevel
) -> None:
    service = _normalize_service(args.service)
    if service in set(config.safety.deny_services):
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Service is denied by tools.gws.safety.deny_services",
            {"service": service},
        )

    if risk == "write" and config.safety.require_prompt_for_write and not ctx.confirm:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "gws write operations require explicit confirmation",
            {
                "risk": "write",
                "suggestion": "Retry with meta.confirm=true or --confirm",
            },
        )

    if risk == "admin" and config.safety.require_prompt_for_admin and not ctx.confirm:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "gws admin operations require explicit confirmation",
            {
                "risk": "admin",
                "suggestion": "Retry with meta.confirm=true or --confirm",
            },
        )


def _require_confirm(ctx: RuntimeContext, *, message: str, risk: str) -> None:
    if ctx.confirm:
        return
    raise ToolRuntimeError(
        "POLICY_DENIED",
        message,
        {"risk": risk, "suggestion": "Retry with meta.confirm=true or --confirm"},
    )


def _json_arg(payload: Optional[Dict[str, Any]], *, flag_name: str) -> Optional[str]:
    if payload is None:
        return None
    try:
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", f"{flag_name} payload must be JSON serializable"
        ) from exc


def _pagination_flags(args: GwsCallArgs, config: GwsToolConfig) -> list[str]:
    pagination = args.pagination
    if pagination is None:
        return []

    flags: list[str] = []
    if pagination.page_all:
        if not config.defaults.allow_page_all:
            raise ToolRuntimeError(
                "POLICY_DENIED", "tools.gws.defaults.allow_page_all is disabled"
            )
        flags.append("--page-all")
        limit = (
            pagination.page_limit
            if pagination.page_limit is not None
            else int(config.defaults.page_limit)
        )
        delay = (
            pagination.page_delay_ms
            if pagination.page_delay_ms is not None
            else int(config.defaults.page_delay_ms)
        )
        flags.extend(["--page-limit", str(limit), "--page-delay", str(delay)])
        return flags

    if pagination.page_limit is not None:
        flags.extend(["--page-limit", str(pagination.page_limit)])
    if pagination.page_delay_ms is not None:
        flags.extend(["--page-delay", str(pagination.page_delay_ms)])
    return flags


def _build_call_argv(
    gws_exec: str, args: GwsCallArgs, config: GwsToolConfig
) -> list[str]:
    argv = [gws_exec, args.service, *args.resource_path, args.method]

    params_arg = _json_arg(args.params, flag_name="params")
    if params_arg is not None:
        argv.extend(["--params", params_arg])

    json_arg = _json_arg(args.json_payload, flag_name="json")
    if json_arg is not None:
        argv.extend(["--json", json_arg])

    if args.dry_run:
        argv.append("--dry-run")

    argv.extend(_pagination_flags(args, config))
    return argv


def _run_command(
    *,
    argv: list[str],
    env: Mapping[str, str],
    timeout_seconds: float,
    max_output_parse_bytes: int,
    max_raw_stdout_bytes: int,
    max_raw_stderr_bytes: int,
    disable_raw_stdout: bool,
) -> _CommandResult:
    try:
        with (
            tempfile.TemporaryFile() as stdout_file,
            tempfile.TemporaryFile() as stderr_file,
        ):
            process = subprocess.Popen(
                argv, stdout=stdout_file, stderr=stderr_file, env=dict(env)
            )
            timed_out = False
            try:
                process.wait(timeout=float(timeout_seconds))
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait()

            stdout_file.seek(0, os.SEEK_END)
            stdout_size = int(stdout_file.tell())
            stderr_file.seek(0, os.SEEK_END)
            stderr_size = int(stderr_file.tell())

            stdout_file.seek(0)
            parse_chunk = stdout_file.read(
                min(stdout_size, int(max_output_parse_bytes))
            )
            stdout_for_parse = parse_chunk.decode("utf-8", errors="replace")
            parse_truncated = stdout_size > len(parse_chunk)

            raw_stdout: Optional[str] = None
            if (
                not disable_raw_stdout
                and max_raw_stdout_bytes > 0
                and stdout_size <= max_raw_stdout_bytes
            ):
                stdout_file.seek(0)
                raw_stdout = stdout_file.read().decode("utf-8", errors="replace")

            stderr_file.seek(0)
            if max_raw_stderr_bytes <= 0:
                raw_stderr = ""
            elif stderr_size <= max_raw_stderr_bytes:
                raw_stderr = stderr_file.read().decode("utf-8", errors="replace")
            else:
                raw_stderr = stderr_file.read(max_raw_stderr_bytes).decode(
                    "utf-8", errors="replace"
                )
                raw_stderr = f"{raw_stderr}\n...[truncated]"

            exit_code = int(
                process.returncode if process.returncode is not None else -1
            )
            return _CommandResult(
                exit_code=exit_code,
                timed_out=timed_out,
                stdout_for_parse=stdout_for_parse,
                stdout_parse_truncated=parse_truncated,
                raw_stdout=raw_stdout,
                raw_stderr=raw_stderr,
                stdout_bytes=stdout_size,
                stderr_bytes=stderr_size,
            )
    except FileNotFoundError as exc:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "gws executable could not be started",
            {"argv0": argv[0]},
        ) from exc
    except OSError as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR", f"gws execution failed: {exc}", {"argv0": argv[0]}
        ) from exc


def _execute_common(
    *,
    ctx: RuntimeContext,
    config: GwsToolConfig,
    tool_name: str,
    argv: list[str],
    request_payload: Dict[str, Any],
    timeout_seconds: float,
    expect_large_output: bool,
    redaction_mode: str,
    include_raw_stdout: bool = True,
) -> Dict[str, Any]:
    env, auth_env = _build_exec_env(config, ctx)
    request_event = _base_request_payload(
        tool=tool_name, command=argv, request=request_payload, auth_env=auth_env
    )
    _emit_event(ctx, event_name="tool.request", payload=request_event)

    started = time.monotonic()
    executed = _run_command(
        argv=argv,
        env=env,
        timeout_seconds=timeout_seconds,
        max_output_parse_bytes=config.max_output_parse_bytes,
        max_raw_stdout_bytes=config.max_raw_stdout_bytes,
        max_raw_stderr_bytes=config.max_raw_stderr_bytes,
        disable_raw_stdout=expect_large_output or (not include_raw_stdout),
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    prefer_ndjson = "--page-all" in argv
    parsed_data, data_format = _parse_json_or_ndjson(
        executed.stdout_for_parse, prefer_ndjson=prefer_ndjson
    )
    ok = not executed.timed_out and executed.exit_code == 0

    if ok:
        content = f"{tool_name} completed ({_summarize_data(parsed_data, data_format=data_format)})"
        error = None
    else:
        error = _extract_error_payload(
            parsed_data,
            executed.raw_stderr,
            timed_out=executed.timed_out,
            exit_code=executed.exit_code,
        )
        content = f"{tool_name} failed ({error['code']})"

    result: Dict[str, Any] = {
        "ok": ok,
        "source": "gws",
        "content": content,
        "data": parsed_data,
        "data_format": data_format,
        "raw_stdout": executed.raw_stdout if include_raw_stdout else None,
        "raw_stderr": executed.raw_stderr,
        "error": error,
        "metrics": {
            "duration_ms": elapsed_ms,
            "exit_code": executed.exit_code,
            "timed_out": executed.timed_out,
            "stdout_bytes": executed.stdout_bytes,
            "stderr_bytes": executed.stderr_bytes,
            "stdout_parse_truncated": executed.stdout_parse_truncated,
        },
    }
    _emit_event(
        ctx,
        event_name="tool.result",
        payload={
            "tool": tool_name,
            "result": _result_for_event(result, redaction_mode=redaction_mode),
        },
    )
    return result


def _resolve_redaction_mode(config: GwsToolConfig, override: Optional[str]) -> str:
    token = str(override or "").strip().lower()
    if token in {"none", "basic", "strict"}:
        return token
    return str(config.safety.redaction_mode or "basic")


def _sanitize_call_request(args: GwsCallArgs, *, risk: RiskLevel) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "service": args.service,
        "resource_path": list(args.resource_path),
        "method": args.method,
        "dry_run": bool(args.dry_run),
        "expect_large_output": bool(args.expect_large_output),
        "risk": risk,
    }
    if args.pagination is not None:
        request["pagination"] = args.pagination.model_dump(exclude_none=True)
    if args.params is not None:
        request["params_keys"] = sorted([str(key) for key in args.params.keys()])
    if args.json_payload is not None:
        request["json_keys"] = sorted([str(key) for key in args.json_payload.keys()])
    if args.force_risk is not None:
        request["force_risk"] = args.force_risk
    return request


def _h_call(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = GwsCallArgs.model_validate(args)
    config = _resolve_config(ctx)
    if not config.enabled:
        raise ToolRuntimeError(
            "POLICY_DENIED", "gws tool is disabled by policy (tools.gws.enabled=false)"
        )

    risk = _classify_risk(validated)
    _ensure_call_allowed(args=validated, ctx=ctx, config=config, risk=risk)

    gws_exec = _resolve_gws_executable(config)
    argv = _build_call_argv(gws_exec, validated, config)
    timeout_seconds = float(
        validated.timeout_seconds or config.defaults.timeout_seconds
    )
    redaction_mode = _resolve_redaction_mode(config, validated.redaction_mode)
    request_payload = _sanitize_call_request(validated, risk=risk)

    result = _execute_common(
        ctx=ctx,
        config=config,
        tool_name=_TOOL_CALL,
        argv=argv,
        request_payload=request_payload,
        timeout_seconds=timeout_seconds,
        expect_large_output=bool(validated.expect_large_output),
        redaction_mode=redaction_mode,
    )
    result["risk"] = risk
    return result


def _h_schema(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = GwsSchemaArgs.model_validate(args)
    config = _resolve_config(ctx)
    if not config.enabled:
        raise ToolRuntimeError(
            "POLICY_DENIED", "gws tool is disabled by policy (tools.gws.enabled=false)"
        )

    gws_exec = _resolve_gws_executable(config)
    argv = [gws_exec, "schema", validated.method_full]
    timeout_seconds = float(
        validated.timeout_seconds or config.defaults.timeout_seconds
    )
    redaction_mode = _resolve_redaction_mode(config, validated.redaction_mode)

    result = _execute_common(
        ctx=ctx,
        config=config,
        tool_name=_TOOL_SCHEMA,
        argv=argv,
        request_payload={"method_full": validated.method_full},
        timeout_seconds=timeout_seconds,
        expect_large_output=False,
        redaction_mode=redaction_mode,
    )
    if result.get("ok"):
        result["content"] = f"Schema fetched for {validated.method_full}"
    return result


def _run_auth_command(
    *,
    ctx: RuntimeContext,
    tool_name: str,
    argv_tail: list[str],
    timeout_seconds: Optional[float],
    redaction_mode_override: Optional[str],
    require_confirm: bool,
) -> Dict[str, Any]:
    config = _resolve_config(ctx)
    if not config.enabled:
        raise ToolRuntimeError(
            "POLICY_DENIED", "gws tool is disabled by policy (tools.gws.enabled=false)"
        )
    if require_confirm:
        _require_confirm(
            ctx, message=f"{tool_name} requires explicit confirmation", risk="admin"
        )

    gws_exec = _resolve_gws_executable(config)
    timeout = float(timeout_seconds or config.defaults.timeout_seconds)
    redaction_mode = _resolve_redaction_mode(config, redaction_mode_override)

    argv = [gws_exec, *argv_tail]
    return _execute_common(
        ctx=ctx,
        config=config,
        tool_name=tool_name,
        argv=argv,
        request_payload={"argv_tail": list(argv_tail)},
        timeout_seconds=timeout,
        expect_large_output=False,
        redaction_mode=redaction_mode,
    )


def _resolve_output_path(ctx: RuntimeContext, output_path: str) -> Path:
    workspace_root = Path(ctx.workspace).expanduser().resolve(strict=False)
    candidate = Path(output_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve(strict=False)
    ctx.policy.ensure_path_allowed(
        str(resolved), workspace=workspace_root, operation="write"
    )
    return resolved


def _h_auth_setup(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = GwsAuthArgs.model_validate(args)
    return _run_auth_command(
        ctx=ctx,
        tool_name=_TOOL_AUTH_SETUP,
        argv_tail=["auth", "setup"],
        timeout_seconds=validated.timeout_seconds,
        redaction_mode_override=validated.redaction_mode,
        require_confirm=True,
    )


def _h_auth_login(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = GwsAuthArgs.model_validate(args)
    return _run_auth_command(
        ctx=ctx,
        tool_name=_TOOL_AUTH_LOGIN,
        argv_tail=["auth", "login"],
        timeout_seconds=validated.timeout_seconds,
        redaction_mode_override=validated.redaction_mode,
        require_confirm=False,
    )


def _auth_export_result_metrics(
    executed: _CommandResult, duration_ms: int
) -> dict[str, Any]:
    return {
        "duration_ms": duration_ms,
        "exit_code": executed.exit_code,
        "timed_out": executed.timed_out,
        "stdout_bytes": executed.stdout_bytes,
        "stderr_bytes": executed.stderr_bytes,
        "stdout_parse_truncated": executed.stdout_parse_truncated,
    }


def _auth_export_success_result(
    *,
    output_path: Path,
    executed: _CommandResult,
    parsed_data: Any,
    data_format: Optional[str],
    duration_ms: int,
) -> Dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_bytes = executed.stdout_for_parse.encode("utf-8", errors="replace")
    output_path.write_bytes(credentials_bytes)
    output_sha = hashlib.sha256(credentials_bytes).hexdigest()
    return {
        "ok": True,
        "source": "gws",
        "content": f"Credentials exported to {output_path}",
        "data": {
            "output_path": str(output_path),
            "bytes": len(credentials_bytes),
            "sha256": output_sha,
        },
        "data_format": "json" if isinstance(parsed_data, Mapping) else data_format,
        "raw_stdout": None,
        "raw_stderr": executed.raw_stderr,
        "error": None,
        "metrics": _auth_export_result_metrics(executed, duration_ms),
    }


def _auth_export_failure_result(
    *,
    parsed_data: Any,
    executed: _CommandResult,
    data_format: Optional[str],
    duration_ms: int,
) -> Dict[str, Any]:
    error = _extract_error_payload(
        parsed_data,
        executed.raw_stderr,
        timed_out=executed.timed_out,
        exit_code=executed.exit_code,
    )
    return {
        "ok": False,
        "source": "gws",
        "content": f"{_TOOL_AUTH_EXPORT} failed ({error['code']})",
        "data": None,
        "data_format": data_format,
        "raw_stdout": None,
        "raw_stderr": executed.raw_stderr,
        "error": error,
        "metrics": _auth_export_result_metrics(executed, duration_ms),
    }


def _h_auth_export(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = GwsAuthExportArgs.model_validate(args)
    _require_confirm(
        ctx, message="gws.auth.export requires explicit confirmation", risk="admin"
    )

    config = _resolve_config(ctx)
    if not config.enabled:
        raise ToolRuntimeError(
            "POLICY_DENIED", "gws tool is disabled by policy (tools.gws.enabled=false)"
        )

    output_path = _resolve_output_path(ctx, validated.output_path)
    if output_path.exists() and not validated.overwrite:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "output_path already exists; set overwrite=true to replace it",
            {"output_path": str(output_path)},
        )

    gws_exec = _resolve_gws_executable(config)
    timeout = float(validated.timeout_seconds or config.defaults.timeout_seconds)
    redaction_mode = _resolve_redaction_mode(config, validated.redaction_mode)

    env, auth_env = _build_exec_env(config, ctx)
    request_payload = {
        "tool": _TOOL_AUTH_EXPORT,
        "source": "gws",
        "command": [gws_exec, "auth", "export", "--unmasked"],
        "request": {
            "output_path": str(output_path),
            "overwrite": bool(validated.overwrite),
        },
        "auth_env": auth_env,
    }
    _emit_event(ctx, event_name="tool.request", payload=request_payload)

    started = time.monotonic()
    executed = _run_command(
        argv=[gws_exec, "auth", "export", "--unmasked"],
        env=env,
        timeout_seconds=timeout,
        max_output_parse_bytes=config.max_output_parse_bytes,
        max_raw_stdout_bytes=0,  # never retain credential payload in memory result
        max_raw_stderr_bytes=config.max_raw_stderr_bytes,
        disable_raw_stdout=True,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    parsed_data, data_format = _parse_json_or_ndjson(
        executed.stdout_for_parse, prefer_ndjson=False
    )
    ok = not executed.timed_out and executed.exit_code == 0

    if ok:
        result = _auth_export_success_result(
            output_path=output_path,
            executed=executed,
            parsed_data=parsed_data,
            data_format=data_format,
            duration_ms=duration_ms,
        )
    else:
        result = _auth_export_failure_result(
            parsed_data=parsed_data,
            executed=executed,
            data_format=data_format,
            duration_ms=duration_ms,
        )

    _emit_event(
        ctx,
        event_name="tool.result",
        payload={
            "tool": _TOOL_AUTH_EXPORT,
            "result": _result_for_event(result, redaction_mode=redaction_mode),
        },
    )
    return result


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=_TOOL_CALL,
            args_model=GwsCallArgs,
            min_scope="READ_ONLY",
            handler=_h_call,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "gws", "google-workspace"),
            capabilities=(
                "tool.execute",
                "tool.gws.read",
                "tool.gws.write",
                "tool.gws.admin",
            ),
        )
    )
    registry.add(
        ToolSpec(
            name=_TOOL_SCHEMA,
            args_model=GwsSchemaArgs,
            min_scope="READ_ONLY",
            handler=_h_schema,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "gws", "google-workspace", "schema"),
            capabilities=("tool.execute", "tool.gws.read"),
        )
    )
    registry.add(
        ToolSpec(
            name=_TOOL_AUTH_SETUP,
            args_model=GwsAuthArgs,
            min_scope="POWER_USER",
            handler=_h_auth_setup,
            dangerous=True,
            idempotent=False,
            tags=("plugin", "gws", "google-workspace", "auth"),
            capabilities=("tool.execute", "tool.gws.admin"),
        )
    )
    registry.add(
        ToolSpec(
            name=_TOOL_AUTH_LOGIN,
            args_model=GwsAuthArgs,
            min_scope="WRITE_SAFE",
            handler=_h_auth_login,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "gws", "google-workspace", "auth"),
            capabilities=("tool.execute", "tool.gws.read"),
        )
    )
    registry.add(
        ToolSpec(
            name=_TOOL_AUTH_EXPORT,
            args_model=GwsAuthExportArgs,
            min_scope="POWER_USER",
            handler=_h_auth_export,
            dangerous=True,
            idempotent=False,
            tags=("plugin", "gws", "google-workspace", "auth"),
            capabilities=("tool.execute", "tool.gws.admin"),
        )
    )


class GwsToolPlugin:
    tool_id = _TOOL_CALL
    capabilities = ("tool.execute", "tool.gws.read", "tool.gws.write", "tool.gws.admin")
    input_schema: Dict[str, Any] = {
        _TOOL_CALL: GWS_CALL_INPUT_SCHEMA,
        _TOOL_SCHEMA: GWS_SCHEMA_INPUT_SCHEMA,
        _TOOL_AUTH_SETUP: GWS_AUTH_INPUT_SCHEMA,
        _TOOL_AUTH_LOGIN: GWS_AUTH_INPUT_SCHEMA,
        _TOOL_AUTH_EXPORT: GWS_AUTH_EXPORT_INPUT_SCHEMA,
    }
    output_schema: Dict[str, Any] = GWS_RESULT_OUTPUT_SCHEMA
    contract_version: str = GWS_INTERFACE_VERSION

    def register(self, registry: ToolRegistry) -> None:
        register(registry)

    def healthcheck(self) -> Dict[str, Any]:
        gws_resolved = shutil.which(GWS_DEFAULT_EXECUTABLE)
        return {
            "ok": True,
            "configured": bool(gws_resolved),
            "gws_path": gws_resolved or "",
            "methods": list(TOOL_DESCRIPTOR["methods"]),
        }


__all__ = [
    "GwsToolPlugin",
    "TOOL_DESCRIPTOR",
    "register",
    "_h_call",
    "_h_schema",
    "_h_auth_setup",
    "_h_auth_login",
    "_h_auth_export",
    "gws_redacted_credential_placeholder",
]
