from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.base.config.env.subprocess import build_subprocess_env
from openminion.base.redaction import redact_sensitive_text
from openminion.modules.tool.contracts.dependencies import (
    ToolDependencyDecl,
    ToolDependencyProbeContext,
    ToolDependencySetupHint,
    ToolDependencyStatus,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.constants import TOOL_DEPENDENCY_VERSION_OUTPUT_LIMIT

ConfiguredExecutable = str | Callable[[ToolDependencyProbeContext], str]


def binary_dependency(
    *,
    dependency_id: str,
    executable: ConfiguredExecutable,
    version_args: tuple[str, ...] = ("--version",),
    setup_hints: tuple[ToolDependencySetupHint, ...] = (),
    timeout_seconds: float = 5.0,
) -> ToolDependencyDecl:
    fixed_version_args = tuple(str(item) for item in version_args)
    return ToolDependencyDecl(
        dependency_id=dependency_id,
        probe=partial(
            _probe_binary_dependency,
            dependency_id=dependency_id,
            executable=executable,
            version_args=fixed_version_args,
            setup_hints=setup_hints,
            timeout_seconds=timeout_seconds,
        ),
        preflight=partial(
            _preflight_binary_dependency,
            dependency_id=dependency_id,
            executable=executable,
            setup_hints=setup_hints,
        ),
        setup_hints=setup_hints,
    )


def _probe_binary_dependency(
    context: ToolDependencyProbeContext,
    *,
    dependency_id: str,
    executable: ConfiguredExecutable,
    version_args: tuple[str, ...],
    setup_hints: tuple[ToolDependencySetupHint, ...],
    timeout_seconds: float,
) -> ToolDependencyStatus:
    resolved, resolution_error = _resolve_dependency_executable(context, executable)
    hints = select_setup_hints(setup_hints)
    if resolution_error:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="unhealthy",
            reason_code=resolution_error,
            message=f"{dependency_id} configuration is invalid",
            setup_hints=hints,
        )
    if not resolved:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="missing",
            reason_code="binary_not_found",
            message=f"{dependency_id} is not available",
            setup_hints=hints,
        )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed trusted argv
            (resolved, *version_args),
            cwd=context.workspace,
            env=build_subprocess_env(context.env.snapshot(), inherit_parent=False),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="unhealthy",
            resolved_path=resolved,
            reason_code="version_probe_timeout",
            message=f"{dependency_id} version probe timed out",
            setup_hints=hints,
        )
    except OSError:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="unhealthy",
            resolved_path=resolved,
            reason_code="version_probe_failed",
            message=f"{dependency_id} version probe failed",
            setup_hints=hints,
        )
    version = _bounded_version_line(completed.stdout or completed.stderr)
    if completed.returncode != 0:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="unhealthy",
            resolved_path=resolved,
            version=version,
            reason_code="version_probe_failed",
            message=f"{dependency_id} version probe exited non-zero",
            setup_hints=hints,
        )
    return ToolDependencyStatus(
        dependency_id=dependency_id,
        state="ready",
        resolved_path=resolved,
        version=version,
        message=f"{dependency_id} is ready",
    )


def _preflight_binary_dependency(
    context: ToolDependencyProbeContext,
    *,
    dependency_id: str,
    executable: ConfiguredExecutable,
    setup_hints: tuple[ToolDependencySetupHint, ...],
) -> ToolDependencyStatus:
    resolved, resolution_error = _resolve_dependency_executable(context, executable)
    if resolution_error:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="unhealthy",
            reason_code=resolution_error,
            message=f"{dependency_id} configuration is invalid",
            setup_hints=select_setup_hints(setup_hints),
        )
    if resolved:
        return ToolDependencyStatus(
            dependency_id=dependency_id,
            state="ready",
            resolved_path=resolved,
            message=f"{dependency_id} is ready",
        )
    return ToolDependencyStatus(
        dependency_id=dependency_id,
        state="missing",
        reason_code="binary_not_found",
        message=f"{dependency_id} is not available",
        setup_hints=select_setup_hints(setup_hints),
    )


def dependency_probe_context_from_runtime(context: Any) -> ToolDependencyProbeContext:
    raw_policy = getattr(getattr(context, "policy", None), "raw", {})
    metadata = (
        raw_policy.get("context_metadata", {})
        if isinstance(raw_policy, Mapping)
        else {}
    )
    policy = metadata if isinstance(metadata, Mapping) else {}
    env = resolve_environment_config(env=getattr(context, "env", None))
    return ToolDependencyProbeContext(
        workspace=Path(getattr(context, "workspace", Path.cwd())),
        env=env,
        policy=policy,
    )


def dependency_probe_context_from_api_runtime(
    runtime: Any,
) -> ToolDependencyProbeContext:
    from openminion.modules.tool.runtime.routing import (
        build_runtime_tool_routing_metadata,
    )

    runtime_config = runtime.config.runtime
    return ToolDependencyProbeContext(
        workspace=Path(runtime.tool_workspace_root or Path.cwd()),
        env=resolve_environment_config(runtime_env=runtime_config.env),
        policy=build_runtime_tool_routing_metadata(runtime_config.tools),
    )


def evaluate_tool_dependencies(
    registry: Any,
    *,
    context: ToolDependencyProbeContext,
) -> dict[str, tuple[ToolDependencyStatus, ...]]:
    tools = registry.list()
    cache: dict[str, ToolDependencyStatus] = {}
    report: dict[str, tuple[ToolDependencyStatus, ...]] = {}
    for tool_name, tool in tools.items():
        dependencies = tuple(getattr(tool, "dependencies", ()) or ())
        if not dependencies:
            continue
        statuses: list[ToolDependencyStatus] = []
        for dependency in dependencies:
            status = cache.get(dependency.dependency_id)
            if status is None:
                status = dependency.probe(context)
                cache[dependency.dependency_id] = status
            statuses.append(status)
        report[str(tool_name)] = tuple(statuses)
    return report


def tool_dependency_report_fields(
    runtime: Any,
    registry: Any,
    *,
    readiness: bool,
) -> dict[str, dict[str, Any]]:
    statuses_by_tool = (
        evaluate_tool_dependencies(
            registry,
            context=dependency_probe_context_from_api_runtime(runtime),
        )
        if readiness
        else {}
    )
    fields: dict[str, dict[str, Any]] = {}
    for tool_name, tool in registry.list().items():
        dependencies = tuple(getattr(tool, "dependencies", ()) or ())
        if not dependencies:
            continue
        item = {
            "dependencies": [dependency.dependency_id for dependency in dependencies]
        }
        statuses = statuses_by_tool.get(str(tool_name), ())
        if statuses:
            item["dependency_readiness"] = [status.as_dict() for status in statuses]
        fields[str(tool_name)] = item
    return fields


def enforce_tool_dependencies(tool: Any, *, runtime_context: Any) -> None:
    dependencies = tuple(getattr(tool, "dependencies", ()) or ())
    if not dependencies:
        return
    context = dependency_probe_context_from_runtime(runtime_context)
    failed: list[ToolDependencyStatus] = []
    for dependency in dependencies:
        probe = dependency.preflight or dependency.probe
        status = probe(context)
        if status.state != "ready":
            failed.append(status)
    if not failed:
        return
    raise ToolRuntimeError(
        "DEPENDENCY_MISSING",
        "Tool dependency is not ready",
        {
            "reason_code": "tool_dependency_missing",
            "tool_id": str(getattr(tool, "name", "") or ""),
            "failed_dependencies": [status.as_dict() for status in failed],
        },
    )


def setup_platform(
    *,
    system_name: str | None = None,
    os_release: Mapping[str, str] | None = None,
) -> str:
    system = str(system_name or platform.system()).strip().lower()
    if system == "darwin":
        return "darwin"
    if system == "windows":
        return "windows"
    if system != "linux":
        return "any"
    facts = dict(os_release) if os_release is not None else _linux_os_release()
    tokens = {
        token
        for value in (facts.get("ID", ""), facts.get("ID_LIKE", ""))
        for token in str(value or "").strip().lower().split()
    }
    if tokens & {"debian", "ubuntu", "linuxmint"}:
        return "linux-debian"
    if tokens & {"rhel", "fedora", "centos", "rocky", "almalinux"}:
        return "linux-rhel"
    if "alpine" in tokens:
        return "linux-alpine"
    if tokens & {"arch", "manjaro"}:
        return "linux-arch"
    return "any"


def select_setup_hints(
    hints: tuple[ToolDependencySetupHint, ...],
    *,
    system_name: str | None = None,
    os_release: Mapping[str, str] | None = None,
) -> tuple[ToolDependencySetupHint, ...]:
    selected_platform = setup_platform(
        system_name=system_name,
        os_release=os_release,
    )
    selected = tuple(
        hint for hint in hints if hint.platform in {selected_platform, "any"}
    )
    return selected or tuple(hint for hint in hints if hint.platform == "any")


def _resolve_dependency_executable(
    context: ToolDependencyProbeContext,
    executable: ConfiguredExecutable,
) -> tuple[str, str]:
    try:
        return _resolve_executable(context, executable), ""
    except (TypeError, ValueError):
        return "", "invalid_dependency_configuration"


def _resolve_executable(
    context: ToolDependencyProbeContext,
    executable: ConfiguredExecutable,
) -> str:
    configured = executable(context) if callable(executable) else executable
    candidate = str(configured or "").strip()
    if not candidate:
        return ""
    if Path(candidate).is_absolute() or os.sep in candidate:
        path = Path(candidate).expanduser().resolve(strict=False)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else ""
    resolved = shutil.which(candidate, path=context.env.get("PATH", ""))
    return str(Path(resolved).resolve(strict=False)) if resolved else ""


def _bounded_version_line(output: str) -> str:
    line = next(
        (item.strip() for item in str(output or "").splitlines() if item.strip()), ""
    )
    redacted, _ = redact_sensitive_text(line)
    return redacted[:TOOL_DEPENDENCY_VERSION_OUTPUT_LIMIT]


def _linux_os_release() -> dict[str, str]:
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    facts: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in {"ID", "ID_LIKE"}:
            facts[key] = value.strip().strip("\"'")
    return facts


__all__ = [
    "binary_dependency",
    "dependency_probe_context_from_api_runtime",
    "dependency_probe_context_from_runtime",
    "enforce_tool_dependencies",
    "evaluate_tool_dependencies",
    "select_setup_hints",
    "setup_platform",
    "tool_dependency_report_fields",
]
