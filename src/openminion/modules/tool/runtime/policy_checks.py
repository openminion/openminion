from pathlib import Path
import shlex
from typing import Any, Dict

from .policy import Policy, canonical_tool_name
from .dangerous import detect_dangerous_command
from ..registry.catalog import ToolSpec
from ..contracts.schemas import Scope


def run_policy_preflight(
    *,
    policy: Policy,
    tool_spec: ToolSpec,
    tool_name: str,
    args: Dict[str, Any],
    effective_scope: Scope,
    confirm: bool,
    workspace: Path,
) -> None:
    """Run all policy checks required before executing a tool."""

    canonical_name = canonical_tool_name(tool_name)

    policy.ensure_tool_allowed(canonical_name)
    policy.ensure_scope_allowed(effective_scope, tool_spec.min_scope, canonical_name)
    policy.ensure_confirm_if_required(
        canonical_name, args, confirm, tool_spec.dangerous
    )

    if canonical_name in ("cmd.run", "exec.run"):
        if canonical_name == "exec.run":
            raw_command = str(args.get("command", "") or "")
            argv = shlex.split(raw_command) if raw_command else []
        else:
            argv = list(args.get("argv", []))
        policy.ensure_command_allowed(argv)
        policy.ensure_exec_allowed(argv=argv, workspace=workspace, confirm=confirm)
        match = detect_dangerous_command(argv, cwd=args.get("cwd"))
        policy.ensure_dangerous_allowed(
            dangerous=match.dangerous,
            pattern_id=match.pattern_id,
            reason=match.reason,
            confirm=confirm,
        )
        if canonical_name == "exec.run":
            workdir = args.get("workdir") or args.get("cwd") or "."
            policy.ensure_path_allowed(str(workdir), workspace, "read")
        else:
            policy.ensure_path_allowed(str(args.get("cwd", ".")), workspace, "read")
        return

    if canonical_name in ("file.list_dir", "file.read", "file.find"):
        key = "root" if canonical_name == "file.find" else "path"
        policy.ensure_path_allowed(str(args.get(key, ".")), workspace, "read")
        return

    if canonical_name in ("file.write", "file.delete"):
        policy.ensure_path_allowed(str(args.get("path", ".")), workspace, "write")
        return

    if canonical_name == "file.copy":
        policy.ensure_path_allowed(str(args.get("src", ".")), workspace, "read")
        policy.ensure_path_allowed(str(args.get("dst", ".")), workspace, "write")
        return

    if canonical_name == "file.move":
        policy.ensure_path_allowed(str(args.get("src", ".")), workspace, "write")
        policy.ensure_path_allowed(str(args.get("dst", ".")), workspace, "write")
