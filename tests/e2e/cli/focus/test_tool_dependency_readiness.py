from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from openminion.api.runtime import APIRuntime
from openminion.api.server import dispatch_request
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from tests.e2e.cli.focus.harness import FocusProbe

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(180)]


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _status_by_id(payload: dict) -> dict[str, dict]:
    return {
        status["dependency_id"]: status
        for profile in payload["exposure"]["profiles"]
        for status in profile.get("dependency_statuses", [])
    }


def test_focus_cli_controlplane_and_invocation_dependency_readiness(
    focus_probe: FocusProbe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    clean_fixture = (
        focus_probe.openminion_root / "tests/tools/security/fixtures/trivy/clean.json"
    )
    gws = _executable(bin_dir / "gws", "printf 'gws 1.0\\n'")
    trivy = _executable(
        bin_dir / "trivy",
        f"if [ \"${{1:-}}\" = --version ]; then printf 'Version: 0.70.0\\n'; "
        f"else cat {clean_fixture!s}; fi",
    )
    missing_semgrep = bin_dir / "missing-semgrep"
    path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("OPENMINION_SECURITY_TRIVY_EXECUTABLE", str(trivy))
    monkeypatch.setenv("OPENMINION_SECURITY_SEMGREP_EXECUTABLE", str(missing_semgrep))

    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        focus_status = focus_probe.run_slash(
            session,
            "/tools status",
            marker="Run /tools status again",
        )
    assert "security_readonly" in focus_status
    assert "degraded" in focus_status
    assert "binary:trivy: ready" in focus_status
    assert "binary:semgrep: missing" in focus_status
    assert "gws.call: ready (binary:gws)" in focus_status

    command = (
        str(focus_probe.python_bin),
        "-m",
        "openminion",
        "--config",
        str(focus_probe.config_path),
        "--no-update-check",
        "tools",
        "list",
        "--readiness",
        "--runtime-source",
        "inproc",
    )
    cli = subprocess.run(  # noqa: S603 - fixed local E2E argv
        command,
        cwd=focus_probe.openminion_root,
        env={**os.environ, **focus_probe.environment()},
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cli.returncode == 0, cli.stderr
    cli_payload = json.loads(cli.stdout)
    gws_tools = [
        item for item in cli_payload["tools"] if item["name"].startswith("gws.")
    ]
    assert len(gws_tools) == 5
    assert all(item["dependencies"] == ["binary:gws"] for item in gws_tools)
    assert all(
        item["dependency_readiness"][0]["resolved_path"] == str(gws)
        for item in gws_tools
    )

    runtime = APIRuntime.from_config_path(str(focus_probe.config_path))
    try:
        status_code, inventory = dispatch_request(
            "GET",
            "/v1/tools",
            str(focus_probe.config_path),
            query="readiness=true",
            runtime=runtime,
        )
        assert int(status_code) == 200
        controlplane_gws = [
            item for item in inventory["tools"] if item["name"].startswith("gws.")
        ]
        assert controlplane_gws == gws_tools

        exposure_code, exposure = dispatch_request(
            "GET",
            "/v1/tools/exposure",
            str(focus_probe.config_path),
            query="session_id=tdrs-e2e",
            runtime=runtime,
        )
        assert int(exposure_code) == 200
        statuses = _status_by_id(exposure)
        assert statuses["binary:trivy"]["state"] == "ready"
        assert statuses["binary:semgrep"]["state"] == "missing"

        runtime.activate_tool_profile(
            "security_readonly", session_id="tdrs-e2e", approved=True
        )
        context = ToolExecutionContext(
            channel="console",
            target="tdrs-e2e",
            session_id="tdrs-e2e",
            metadata={
                "workspace_root": str(focus_probe.openminion_root),
                "runtime_env": dict(runtime.config.runtime.env or {}),
                **build_runtime_tool_routing_metadata(runtime.config.runtime.tools),
            },
        )
        results = runtime.tools.execute_calls(
            [
                ProviderToolCall(
                    name="security.scan_dependencies",
                    arguments={
                        "target": "tests/tools/security/fixtures/targets/dependencies"
                    },
                    source="tdrs-e2e",
                ),
                ProviderToolCall(
                    name="security.scan_code",
                    arguments={"target": "tests/tools/security/fixtures/targets/code"},
                    source="tdrs-e2e",
                ),
            ],
            context=context,
        ).results
        assert results[0].ok is True
        assert results[1].ok is False
        assert results[1].data["error_code"] == "DEPENDENCY_MISSING"
        assert (
            results[1].data["details"]["failed_dependencies"][0]["dependency_id"]
            == "binary:semgrep"
        )

        semgrep = _executable(bin_dir / "semgrep", "printf 'semgrep 1.0\\n'")
        monkeypatch.setenv("OPENMINION_SECURITY_SEMGREP_EXECUTABLE", str(semgrep))
        refreshed = runtime.tool_exposure_status(session_id="tdrs-e2e")
        refreshed_statuses = {
            status["dependency_id"]: status
            for profile in refreshed["profiles"]
            for status in profile.get("dependency_statuses", [])
        }
        assert refreshed_statuses["binary:semgrep"]["state"] == "ready"
    finally:
        runtime.close()
