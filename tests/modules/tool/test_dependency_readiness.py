from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from openminion.api.queries.runtime_reports import build_tool_inventory_report
from openminion.base.config import OpenMinionConfig
from openminion.base.config import ToolRuntimeConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.modules.tool.contracts.dependencies import (
    ToolDependencyDecl,
    ToolDependencyProbeContext,
    ToolDependencySetupHint,
    ToolDependencyStatus,
)
from openminion.modules.tool.framework import (
    ToolDecl,
    ToolFamilySpec,
    derive_tool_specs,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.modules.tool.runtime.dependencies import (
    binary_dependency,
    enforce_tool_dependencies,
    evaluate_tool_dependencies,
    select_setup_hints,
    setup_platform,
)
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.gws.plugin import GwsToolPlugin


class _Args(BaseModel):
    value: str = ""


def _context(tmp_path: Path, *, path: str = "") -> ToolDependencyProbeContext:
    return ToolDependencyProbeContext(
        workspace=tmp_path,
        env=EnvironmentConfig.from_sources(process_env={"PATH": path}),
        policy={},
    )


def _executable(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _hint(platform: str = "any") -> ToolDependencySetupHint:
    return ToolDependencySetupHint(
        platform=platform,
        label="Official setup",
        official_url="https://example.com/install",
    )


def test_dependency_contracts_validate_and_derive() -> None:
    status = ToolDependencyStatus(dependency_id="binary:demo", state="ready")
    dependency = ToolDependencyDecl(
        dependency_id="binary:demo",
        probe=lambda _context: status,
    )
    family = ToolFamilySpec(
        module_id="demo",
        tools=(
            ToolDecl(
                "demo.read", _Args, lambda _args, _ctx: {}, dependencies=(dependency,)
            ),
        ),
    )

    spec = derive_tool_specs(family)[0]

    assert spec.dependencies == (dependency,)
    assert (
        ToolSpec("direct", _Args, "READ_ONLY", lambda _args, _ctx: {}).dependencies
        == ()
    )
    with pytest.raises(ToolRuntimeError, match="invalid tool dependency id"):
        ToolDependencyDecl("demo", lambda _context: status)
    with pytest.raises(ToolRuntimeError, match="official_url"):
        ToolDependencySetupHint(
            platform="any", label="Bad", official_url="http://example.com"
        )


def test_binary_dependency_resolves_configured_path_and_path(tmp_path: Path) -> None:
    executable = _executable(tmp_path, "demo", "printf 'demo 1.2.3\\n'")
    configured = binary_dependency(
        dependency_id="binary:configured",
        executable=str(executable),
    )
    discovered = binary_dependency(
        dependency_id="binary:discovered",
        executable="demo",
    )

    configured_status = configured.probe(_context(tmp_path))
    discovered_status = discovered.probe(_context(tmp_path, path=str(tmp_path)))

    assert configured_status.state == "ready"
    assert configured_status.version == "demo 1.2.3"
    assert discovered_status.resolved_path == str(executable.resolve())


def test_binary_dependency_reports_missing_failure_timeout_and_redaction(
    tmp_path: Path,
) -> None:
    failed = _executable(
        tmp_path,
        "failed",
        "printf 'token=top-secret and more output\\n' >&2; exit 2",
    )
    slow = _executable(tmp_path, "slow", "sleep 1")

    def _invalid_executable(_context: ToolDependencyProbeContext) -> str:
        raise ValueError("invalid")

    missing_status = binary_dependency(
        dependency_id="binary:missing",
        executable="not-installed",
        setup_hints=(_hint(),),
    ).probe(_context(tmp_path, path=str(tmp_path)))
    failed_status = binary_dependency(
        dependency_id="binary:failed",
        executable=str(failed),
    ).probe(_context(tmp_path))
    timeout_status = binary_dependency(
        dependency_id="binary:slow",
        executable=str(slow),
        timeout_seconds=0.01,
    ).probe(_context(tmp_path))
    invalid_status = binary_dependency(
        dependency_id="binary:invalid",
        executable=_invalid_executable,
    ).probe(_context(tmp_path))

    assert missing_status.state == "missing"
    assert missing_status.setup_hints[0].official_url == "https://example.com/install"
    assert failed_status.state == "unhealthy"
    assert "top-secret" not in failed_status.version
    assert "[REDACTED]" in failed_status.version
    assert timeout_status.reason_code == "version_probe_timeout"
    assert invalid_status.reason_code == "invalid_dependency_configuration"


def test_status_reuses_dependency_probe_and_preflight_does_not_run_version(
    tmp_path: Path,
) -> None:
    calls = 0

    def _probe(_context: ToolDependencyProbeContext) -> ToolDependencyStatus:
        nonlocal calls
        calls += 1
        return ToolDependencyStatus(dependency_id="binary:shared", state="ready")

    dependency = ToolDependencyDecl("binary:shared", _probe, preflight=_probe)
    registry = ToolRegistry()
    for name in ("one", "two"):
        registry.add(
            ToolSpec(
                name=name,
                args_model=_Args,
                min_scope="READ_ONLY",
                handler=lambda _args, _ctx: {},
                dependencies=(dependency,),
            )
        )

    report = evaluate_tool_dependencies(registry, context=_context(tmp_path))

    assert set(report) == {"one", "two"}
    assert calls == 1

    marker = tmp_path / "ran"
    executable = _executable(tmp_path, "presence", f"touch {marker}; printf '1\\n'")
    binary = binary_dependency(
        dependency_id="binary:presence",
        executable=str(executable),
    )
    runtime_context = SimpleNamespace(
        workspace=tmp_path,
        env=EnvironmentConfig.from_sources(process_env={}),
        policy=Policy(raw={"context_metadata": {}}),
    )
    enforce_tool_dependencies(
        SimpleNamespace(name="presence", dependencies=(binary,)),
        runtime_context=runtime_context,
    )
    assert not marker.exists()


def test_missing_dependency_stops_before_handler(tmp_path: Path) -> None:
    handler_started = False

    def _handler(_args, _ctx):
        nonlocal handler_started
        handler_started = True
        return {"ok": True}

    tool = ToolSpec(
        name="demo.missing",
        args_model=_Args,
        min_scope="READ_ONLY",
        handler=_handler,
        dependencies=(
            binary_dependency(
                dependency_id="binary:not-installed",
                executable="not-installed",
                setup_hints=(_hint(),),
            ),
        ),
    )

    result = execute_tool_spec_call(
        tool=tool,
        arguments={},
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="dependency-missing",
            metadata={"workspace_root": str(tmp_path), "runtime_env": {"PATH": ""}},
        ),
    )

    assert result.ok is False
    assert handler_started is False
    assert result.data["error_code"] == "DEPENDENCY_MISSING"
    assert result.data["details"]["reason_code"] == "tool_dependency_missing"
    failed = result.data["details"]["failed_dependencies"]
    assert failed[0]["dependency_id"] == "binary:not-installed"
    assert failed[0]["setup_hints"][0]["label"] == "Official setup"


def test_inventory_probes_only_when_readiness_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _executable(tmp_path, "gws", "printf 'gws 1.0\\n'")
    registry = ToolRegistry()
    GwsToolPlugin().register(registry)
    config = OpenMinionConfig()
    config.runtime.tools = ToolRuntimeConfig(gws={"gws_path": str(executable)})
    runtime = SimpleNamespace(
        tools=registry,
        config=config,
        tool_workspace_root=tmp_path,
    )
    from openminion.modules.tool.runtime import dependencies as dependency_runtime

    real_run = dependency_runtime.subprocess.run
    calls = 0

    def _run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(dependency_runtime.subprocess, "run", _run)

    static = build_tool_inventory_report(runtime)
    assert calls == 0
    assert len(static) == 5
    assert all(item["dependencies"] == ["binary:gws"] for item in static)
    assert all("dependency_readiness" not in item for item in static)

    live = build_tool_inventory_report(runtime, readiness=True)
    assert calls == 1
    assert all(item["dependency_readiness"][0]["version"] == "gws 1.0" for item in live)


@pytest.mark.parametrize(
    ("system_name", "os_release", "expected"),
    [
        ("Darwin", {}, "darwin"),
        ("Windows", {}, "windows"),
        ("Linux", {"ID": "ubuntu"}, "linux-debian"),
        ("Linux", {"ID_LIKE": "rhel fedora"}, "linux-rhel"),
        ("Linux", {"ID": "alpine"}, "linux-alpine"),
        ("Linux", {"ID": "arch"}, "linux-arch"),
        ("Linux", {"ID": "unknown"}, "any"),
    ],
)
def test_setup_platform_selection(
    system_name: str,
    os_release: dict[str, str],
    expected: str,
) -> None:
    hints = (_hint(expected), _hint("any")) if expected != "any" else (_hint(),)

    assert setup_platform(system_name=system_name, os_release=os_release) == expected
    assert select_setup_hints(
        hints,
        system_name=system_name,
        os_release=os_release,
    )
