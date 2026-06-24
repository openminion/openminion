import hashlib
import re
import shlex
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

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
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.brain.runtime.escalation import (
    ActionRiskTier,
)
from openminion.tools.exec.hints import READ_ONLY_DISCOVERY_HINTS

from .command_parser import (
    CommandParseError,
    ParseResult,
    parse_command,
)
from .constants import (
    EXEC_AGENT_ID_ENV,
    EXEC_ALLOWLIST_PATHS_ENV,
    EXEC_APPROVAL_PENDING_STATUSES,
    EXEC_ARTIFACT_THRESHOLD_BYTES,
    EXEC_DEBUG_PARSE_EVENT_ENV,
    EXEC_DENY_HOST_ENV_PREFIXES,
    EXEC_ENABLE_HOST_EXEC_ENV,
    EXEC_SAFE_BINS_DEFAULT,
    EXEC_SAFE_BIN_TRUSTED_DIRS_DEFAULT,
    EXEC_SAFE_BINS_ENV,
    EXEC_SAFE_BIN_TRUSTED_DIRS_ENV,
)
from .process import ShellFamily, resolve_shell_family

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


def _attach_parse_error_hint(details: Dict[str, Any], code: str) -> Dict[str, Any]:
    if str(code or "").strip() == "unsupported_redirection":
        command = str(details.get("command", "") or "").strip().lower()
        if "pytest" in command or "python -m" in command:
            details.setdefault(
                "suggested_tool", _UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_TOOL
            )
            details.setdefault(
                "suggested_fix", _UNSUPPORTED_COMMAND_OUTPUT_REDIRECTION_HINT_FIX
            )
        else:
            details.setdefault("suggested_tool", _UNSUPPORTED_REDIRECTION_HINT_TOOL)
            details.setdefault("suggested_fix", _UNSUPPORTED_REDIRECTION_HINT_FIX)
    return details


def _attach_executable_denial_hint(
    details: Dict[str, Any],
    executable: str,
) -> Dict[str, Any]:
    normalized = str(executable or "").strip().lower()
    if normalized == "pytest":
        details.setdefault("suggested_tool", _PYTEST_EXECUTABLE_HINT_TOOL)
        details.setdefault("suggested_fix", _PYTEST_EXECUTABLE_HINT_FIX)
    if normalized in {"pip", "pip3"}:
        details.setdefault("suggested_tool", _PACKAGE_INSTALL_HINT_TOOL)
        details.setdefault("suggested_fix", _PACKAGE_INSTALL_HINT_FIX)
    if normalized == "which":
        details.setdefault("suggested_tool", _PYTHON_DISCOVERY_HINT_TOOL)
        details.setdefault("suggested_fix", _PYTHON_DISCOVERY_HINT_FIX)
    return details


def _normalize_bool_env(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_value(ctx: RuntimeContext, key: str, default: str = "") -> str:
    return str(ctx.env.get(key, default) or "").strip()


def _host_execution_enabled(ctx: RuntimeContext) -> bool:
    return _normalize_bool_env(_env_value(ctx, EXEC_ENABLE_HOST_EXEC_ENV, "0"))


def _agent_id(ctx: RuntimeContext) -> str:
    return str(
        ctx.policy.raw.get("agent_id")
        or _env_value(ctx, EXEC_AGENT_ID_ENV)
        or "default-agent"
    )


def _sanitize_command(command: str) -> str:
    normalized = str(command).strip()
    if len(normalized) <= 240:
        return normalized
    return f"{normalized[:240]}...[truncated]"


def _debug_parse_events_enabled(ctx: RuntimeContext) -> bool:
    return _normalize_bool_env(_env_value(ctx, EXEC_DEBUG_PARSE_EVENT_ENV, "0"))


def _emit_parse_debug_event(
    ctx: RuntimeContext,
    *,
    command: str,
    parsed: ParseResult,
    validator: str,
) -> None:
    if not _debug_parse_events_enabled(ctx):
        return
    command_hash = hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()
    emit_family_event(
        ctx,
        event="exec.command_parsed",
        payload={
            "validator": validator,
            "segment_count": len(parsed.segments),
            "operators": list(parsed.operators),
            "command_hash": command_hash,
        },
    )


def _canonicalize_executable_name(executable: str) -> str:
    raw_name = str(executable or "").strip()
    if not raw_name:
        return raw_name
    normalized = _CANONICAL_EXECUTABLE_ALIASES.get(raw_name)
    if normalized and shutil.which(normalized):
        return normalized
    if (
        raw_name == "python"
        and shutil.which("python") is None
        and shutil.which("python3.11")
    ):
        return "python3.11"
    return raw_name


def _normalize_executable_variants(command: str) -> str:
    raw_command = str(command or "").strip()
    if not raw_command:
        return raw_command
    shell_family = resolve_shell_family()
    if shell_family != ShellFamily.POSIX:
        return raw_command
    try:
        parsed = parse_command(raw_command, shell_family=shell_family)
    except CommandParseError:
        return raw_command

    normalized_segments: list[str] = []
    changed = False
    for segment in parsed.segments:
        argv = list(segment.argv)
        canonical_exec = _canonicalize_executable_name(argv[0])
        if canonical_exec != argv[0]:
            argv[0] = canonical_exec
            changed = True
        normalized_segments.append(shlex.join(argv))

    if not changed:
        return raw_command

    rebuilt = normalized_segments[0]
    for operator, segment_text in zip(parsed.operators, normalized_segments[1:]):
        rebuilt = f"{rebuilt} {operator} {segment_text}"
    return rebuilt


def _env_overrides_for_mode(*, host: str, env: Mapping[str, str]) -> tuple[bool, str]:
    if host == "sandbox":
        return True, ""
    for key in env:
        key_upper = str(key).upper()
        if key_upper == "PATH":
            return False, "host execution rejects env override PATH"
        if key_upper.startswith(EXEC_DENY_HOST_ENV_PREFIXES):
            return (
                False,
                "host execution rejects dynamic loader env overrides (LD_*/DYLD_*)",
            )
    return True, ""


def _parse_allowlist_paths_from_env(ctx: RuntimeContext) -> set[str]:
    raw = _env_value(ctx, EXEC_ALLOWLIST_PATHS_ENV)
    if not raw:
        return set()
    return {
        str(Path(item.strip()).expanduser().resolve(strict=False))
        for item in re.split(r"[:,]", raw)
        if item.strip()
    }


def _parse_safe_bins_from_env(ctx: RuntimeContext) -> set[str]:
    raw = _env_value(ctx, EXEC_SAFE_BINS_ENV)
    if not raw:
        return set(EXEC_SAFE_BINS_DEFAULT)
    return {item.strip() for item in raw.split(",") if item.strip()}


def _parse_safe_bin_trusted_dirs_from_env(ctx: RuntimeContext) -> set[str]:
    raw = _env_value(ctx, EXEC_SAFE_BIN_TRUSTED_DIRS_ENV)
    if not raw:
        return set(EXEC_SAFE_BIN_TRUSTED_DIRS_DEFAULT)
    return {
        str(Path(item.strip()).expanduser().resolve(strict=False))
        for item in re.split(r"[:,]", raw)
        if item.strip()
    }


def _is_under_trusted_dir(path: str, trusted_dirs: Iterable[str]) -> bool:
    candidate = Path(path).expanduser().resolve(strict=False)
    for trusted in trusted_dirs:
        trusted_path = Path(str(trusted)).expanduser().resolve(strict=False)
        try:
            candidate.relative_to(trusted_path)
            return True
        except ValueError:
            continue
    return False


def _validate_host_allowlist(
    command: str,
    ctx: RuntimeContext,
) -> tuple[bool, str, Dict[str, Any]]:
    shell_family = resolve_shell_family()
    if shell_family == ShellFamily.UNKNOWN:
        return (
            False,
            "allowlist validation failed: unsupported shell family",
            {
                "command": command,
                "shell_family": shell_family.value,
                "parse_error_code": "unsupported_shell",
                "parse_error_position": None,
            },
        )
    try:
        parsed = parse_command(command, shell_family=shell_family)
    except CommandParseError as exc:
        details = _attach_parse_error_hint(
            {
                "command": command,
                "parse_error_code": exc.code,
                "parse_error_position": exc.position,
            },
            exc.code,
        )
        return (
            False,
            f"allowlist validation failed: {exc.message}",
            details,
        )
    _emit_parse_debug_event(
        ctx,
        command=command,
        parsed=parsed,
        validator="allowlist",
    )

    allowed_paths = _parse_allowlist_paths_from_env(ctx)
    safe_bins = _parse_safe_bins_from_env(ctx)
    trusted_dirs = _parse_safe_bin_trusted_dirs_from_env(ctx)
    checked: list[Dict[str, str]] = []

    for segment in parsed.segments:
        raw_exec = segment.argv[0]
        resolved = raw_exec if Path(raw_exec).is_absolute() else shutil.which(raw_exec)
        if not resolved:
            return (
                False,
                f"allowlist validation failed: executable not found '{raw_exec}'",
                {"segment": segment.raw},
            )

        resolved_path = str(Path(resolved).expanduser().resolve(strict=False))
        exec_name = Path(resolved_path).name
        checked.append(
            {"segment": segment.raw, "exec": exec_name, "path": resolved_path}
        )

        if resolved_path in allowed_paths:
            continue
        if exec_name in safe_bins and _is_under_trusted_dir(
            resolved_path, trusted_dirs
        ):
            continue

        details = _attach_executable_denial_hint(
            {
                "segment": segment.raw,
                "resolved_path": resolved_path,
                "checked": checked,
            },
            exec_name,
        )
        return (
            False,
            f"allowlist validation failed: executable '{exec_name}' is not allowed",
            details,
        )

    return True, "", {"checked": checked}


def _validate_command_against_policy(
    command: str, ctx: RuntimeContext
) -> tuple[bool, str, Dict[str, Any]]:
    shell_family = resolve_shell_family()
    if shell_family == ShellFamily.UNKNOWN:
        return (
            False,
            "exec validation not supported: unsupported shell family",
            {
                "command": command,
                "shell_family": shell_family.value,
                "parse_error_code": "unsupported_shell",
                "parse_error_position": None,
            },
        )
    try:
        parsed = parse_command(command, shell_family=shell_family)
    except CommandParseError as exc:
        details = _attach_parse_error_hint(
            {
                "command": command,
                "parse_error_code": exc.code,
                "parse_error_position": exc.position,
            },
            exc.code,
        )
        return (
            False,
            f"unsupported command syntax: {exc.message}",
            details,
        )
    _emit_parse_debug_event(
        ctx,
        command=command,
        parsed=parsed,
        validator="policy",
    )

    checked: list[Dict[str, Any]] = []
    for segment in parsed.segments:
        executable = str(segment.argv[0])
        try:
            resolved = ctx.policy.ensure_command_allowed([executable])
        except ToolRuntimeError as exc:
            details = dict(exc.details or {})
            if executable == "mkdir":
                details.setdefault("suggested_tool", "file.write")
                details.setdefault(
                    "suggested_fix",
                    (
                        "If you are scaffolding files or folders, write the "
                        "target file directly with file.write; parent "
                        "directories are created automatically by default."
                    ),
                )
            if executable == "cd":
                details.setdefault("suggested_tool", "exec.run")
                if re.search(r"\bpip(?:3)?\s+install\b", command):
                    details.setdefault("suggested_fix", _PACKAGE_INSTALL_HINT_FIX)
                else:
                    details.setdefault(
                        "suggested_fix",
                        (
                            "Do not prefix the command with `cd ... &&`. "
                            "Pass the target directory with the exec.run "
                            "`workdir`/`cwd` argument and keep the command itself "
                            "focused on the executable you want to run."
                        ),
                    )
            if executable in READ_ONLY_DISCOVERY_HINTS:
                hint_tool, hint_fix = READ_ONLY_DISCOVERY_HINTS[executable]
                details.setdefault("suggested_tool", hint_tool)
                details.setdefault("suggested_fix", hint_fix)
            _attach_executable_denial_hint(details, executable)
            if exc.code == "POLICY_DENIED" and details.get("command"):
                message = f"blocked executable: {details.get('command')}"
            else:
                message = exc.message
            return False, message, {"segment": segment.raw, **details}
        checked.append(
            {"segment": segment.raw, "exec": executable, "resolved": resolved}
        )
    return True, "", {"checked": checked}
