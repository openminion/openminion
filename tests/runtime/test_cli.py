from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.services.runtime.cli import main


def _run(args: list[str]) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            code = main(args)
        except SystemExit as exc:
            code = int(exc.code) if exc.code is not None else 0
    return code, buf.getvalue()


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_version_command() -> None:
    code, out = _run(["version"])
    assert code == 0
    data = json.loads(out)
    assert "version" in data
    assert data["module"] == "openminion-runtime"


def test_config_command_produces_json() -> None:
    code, out = _run(["config", "--yaml", "runtime.yaml"])
    assert code == 0
    data = json.loads(out)
    assert "config" in data
    cfg = data["config"]
    assert "max_agents_hot" in cfg
    assert "max_global_concurrency" in cfg
    assert "agent_ttl_seconds" in cfg
    assert "sweep_interval_seconds" in cfg


def test_validate_command_valid_config() -> None:
    code, out = _run(["validate", "--yaml", "runtime.yaml"])
    data = json.loads(out)
    # If file exists, should be valid; if not exists it still succeeds with defaults
    assert code in (0, 0)
    assert data.get("valid") is True or "issues" in data


def test_run_sample_command() -> None:
    code, out = _run(["run-sample"])
    assert code == 0
    data = json.loads(out)
    assert data["status"] == "ok"
    assert "echo:hello" in data["results"]


def test_no_command_prints_help() -> None:
    code, out = _run([])
    assert code == 0


def test_config_missing_yaml_uses_defaults() -> None:
    code, out = _run(["config", "--yaml", "/nonexistent/path/runtime.yaml"])
    assert code == 0
    data = json.loads(out)
    assert data["source"] == "<defaults>"
    assert data["config"]["max_agents_hot"] == 8


def test_openminion_main_missing_config_launches_setup_in_tty() -> None:
    from openminion.cli.main import main as openminion_main
    from openminion.services.bootstrap.onboarding import (
        OnboardingAction,
        OnboardingState,
        OnboardingStatus,
        OnboardingTrack,
    )

    with (
        mock.patch(
            "openminion.cli.main.resolve_surface_onboarding_route",
            return_value=mock.Mock(
                status=OnboardingStatus(
                    state=OnboardingState.MISSING_CONFIG,
                    action=OnboardingAction.LAUNCH_SETUP,
                    track=OnboardingTrack.UNKNOWN,
                    reason="missing config",
                    config_path=Path("/tmp/missing.json"),
                    home_root=Path("/tmp"),
                    data_root=Path("/tmp/.openminion"),
                ),
                should_launch_setup=True,
                should_fail_fast=False,
            ),
        ),
        mock.patch(
            "openminion.cli.commands.setup.run_setup",
            return_value=0,
        ) as setup_mock,
        mock.patch(
            "sys.stdin.isatty",
            return_value=True,
        ),
        mock.patch(
            "sys.stdout.isatty",
            return_value=True,
        ),
    ):
        assert openminion_main([]) == 0

    setup_mock.assert_called_once()


def test_openminion_main_no_interactive_fails_fast_without_launching_setup() -> None:
    from openminion.cli.main import main as openminion_main
    from openminion.services.bootstrap.onboarding import (
        OnboardingAction,
        OnboardingState,
        OnboardingStatus,
        OnboardingTrack,
    )

    with (
        mock.patch(
            "openminion.cli.main.resolve_surface_onboarding_route",
            return_value=mock.Mock(
                status=OnboardingStatus(
                    state=OnboardingState.MISSING_CONFIG,
                    action=OnboardingAction.FAIL_FAST,
                    track=OnboardingTrack.UNKNOWN,
                    reason="missing config",
                    config_path=Path("/tmp/missing.json"),
                    home_root=Path("/tmp"),
                    data_root=Path("/tmp/.openminion"),
                ),
                should_launch_setup=False,
                should_fail_fast=True,
            ),
        ),
        mock.patch("openminion.cli.commands.setup.run_setup") as setup_mock,
    ):
        with pytest.raises(SystemExit) as exc_info:
            openminion_main(["--no-interactive"])

    assert exc_info.value.code == 2
    setup_mock.assert_not_called()


def test_openminion_main_allow_unsandboxed_exec_sets_env_before_runtime_bootstrap():
    from openminion.cli.main import main as openminion_main

    args = SimpleNamespace(
        allow_unsandboxed_exec=True,
        home_root=None,
        data_root=None,
        generated_root=None,
        config=None,
        no_interactive=False,
        needs_app=True,
        handler=lambda parsed_args, app: 0,
    )
    parser = mock.Mock()
    parser.parse_args.return_value = args
    sentinel_runtime = object()

    with (
        mock.patch.dict(os.environ, {}, clear=False),
        mock.patch("openminion.cli.main.build_parser", return_value=parser),
        mock.patch(
            "openminion.api.runtime.APIRuntime.from_config_path",
            return_value=sentinel_runtime,
        ) as runtime_mock,
    ):
        rc = openminion_main(["--allow-unsandboxed-exec"])
        assert os.environ["OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC"] == "1"

    assert rc == 0
    runtime_mock.assert_called_once()
