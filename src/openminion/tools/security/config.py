"""Family-local configuration and approved-root resolution."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.config import (
    get_tool_env,
    get_tool_env_list,
    resolve_tool_workspace_root,
)

SEMGREP_EXECUTABLE_ENV = "OPENMINION_SECURITY_SEMGREP_EXECUTABLE"
_SEMGREP_CONFIG_ENV = "OPENMINION_SECURITY_SEMGREP_CONFIG"
TRIVY_EXECUTABLE_ENV = "OPENMINION_SECURITY_TRIVY_EXECUTABLE"
_ALLOWED_ROOTS_ENV = "OPENMINION_SECURITY_ALLOWED_ROOTS"


@dataclass(frozen=True)
class SecurityConfig:
    workspace_root: Path
    allowed_roots: tuple[Path, ...]
    semgrep_executable: str
    semgrep_config: str
    trivy_executable: str


def resolve_security_config(ctx: Any) -> SecurityConfig:
    workspace_root = resolve_tool_workspace_root(context=ctx)
    configured_roots = get_tool_env_list(
        _ALLOWED_ROOTS_ENV,
        context=ctx,
        separator=os.pathsep,
    )
    allowed_roots = tuple(
        Path(value).expanduser().resolve(strict=False) for value in configured_roots
    ) or (workspace_root,)
    return SecurityConfig(
        workspace_root=workspace_root,
        allowed_roots=allowed_roots,
        semgrep_executable=get_tool_env(
            SEMGREP_EXECUTABLE_ENV, "semgrep", context=ctx
        ).strip(),
        semgrep_config=get_tool_env(_SEMGREP_CONFIG_ENV, "", context=ctx).strip(),
        trivy_executable=get_tool_env(
            TRIVY_EXECUTABLE_ENV, "trivy", context=ctx
        ).strip(),
    )


def resolve_local_target(raw_target: str, config: SecurityConfig) -> Path:
    candidate = Path(raw_target).expanduser()
    if not candidate.is_absolute():
        candidate = config.workspace_root / candidate
    target = candidate.resolve(strict=False)
    if not target.exists():
        raise ToolRuntimeError(
            "NOT_FOUND", "security scan target does not exist", {"target": raw_target}
        )
    if not any(_within(target, root) for root in config.allowed_roots):
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security scan target is outside configured allowed roots",
            {"target": raw_target},
        )
    return target


def display_target(target: Path, config: SecurityConfig) -> str:
    for root in (config.workspace_root, *config.allowed_roots):
        try:
            return target.relative_to(root).as_posix() or "."
        except ValueError:
            continue
    return str(target)


def _within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "SecurityConfig",
    "SEMGREP_EXECUTABLE_ENV",
    "TRIVY_EXECUTABLE_ENV",
    "display_target",
    "resolve_local_target",
    "resolve_security_config",
]
