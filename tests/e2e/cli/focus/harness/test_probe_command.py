from __future__ import annotations

import pytest


import json
import os
from pathlib import Path

from tests.e2e.cli.focus.conftest import _isolated_live_config
from tests.e2e.cli.focus.harness.probe import FocusProbe

pytestmark = pytest.mark.e2e


def test_live_config_uses_private_file_and_separate_daemon_port(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"runtime": {"log_level": "INFO"}}), encoding="utf-8")
    run_root = tmp_path / "run"
    run_root.mkdir()

    isolated = _isolated_live_config(source, run_root)

    assert json.loads(source.read_text(encoding="utf-8")) == {
        "runtime": {"log_level": "INFO"}
    }
    assert json.loads(isolated.read_text(encoding="utf-8"))["runtime"]["ipc_port"] > 0
    assert os.stat(isolated).st_mode & 0o777 == 0o600


def test_focus_probe_adds_demo_flag_for_echo_agent(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"agents": {"openminion": {"provider": "echo"}}}),
        encoding="utf-8",
    )
    probe = FocusProbe(
        python_bin=Path("python"),
        openminion_root=tmp_path,
        framework_root=tmp_path.parent,
        data_root=tmp_path / "data",
        config_path=config,
        agent_id="openminion",
        workdir=tmp_path,
        session_id="s1",
    )

    assert probe.uses_echo_agent() is True
    assert "--demo" in probe.command()


def test_focus_probe_does_not_add_demo_flag_for_real_provider(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"agents": {"minimax": {"provider": "openai"}}}),
        encoding="utf-8",
    )
    probe = FocusProbe(
        python_bin=Path("python"),
        openminion_root=tmp_path,
        framework_root=tmp_path.parent,
        data_root=tmp_path / "data",
        config_path=config,
        agent_id="minimax",
        workdir=tmp_path,
        session_id="s1",
    )

    assert probe.uses_echo_agent() is False
    assert "--demo" not in probe.command()
    assert "--allow-unsandboxed-exec" in probe.command()


def test_focus_probe_can_leave_host_execution_disabled(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"agents": {"minimax": {"provider": "openai"}}}),
        encoding="utf-8",
    )
    probe = FocusProbe(
        python_bin=Path("python"),
        openminion_root=tmp_path,
        framework_root=tmp_path.parent,
        data_root=tmp_path / "data",
        config_path=config,
        agent_id="minimax",
        workdir=tmp_path,
        session_id="s1",
        allow_unsandboxed_exec=False,
    )

    assert "--allow-unsandboxed-exec" not in probe.command()


def test_focus_probe_child_home_is_outside_package_checkout(tmp_path: Path) -> None:
    package_root = tmp_path / "openminion"
    package_root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"agents": {"openminion": {"provider": "echo"}}}),
        encoding="utf-8",
    )
    data_root = tmp_path / "runtime" / "data"
    probe = FocusProbe(
        python_bin=Path("python"),
        openminion_root=package_root,
        framework_root=tmp_path,
        data_root=data_root,
        config_path=config,
        agent_id="openminion",
        workdir=tmp_path,
        session_id="s1",
    )

    environment = probe.environment()

    assert Path(environment["OPENMINION_HOME"]) != package_root
    assert Path(environment["OPENMINION_DATA_ROOT"]) == data_root
    assert Path(environment["OPENMINION_GENERATED_ROOT"]) == data_root / "runtime"


def test_focus_probe_for_session_preserves_roots_and_changes_only_session(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"agents": {"openminion": {"provider": "echo"}}}),
        encoding="utf-8",
    )
    probe = FocusProbe(
        python_bin=Path("python"),
        openminion_root=tmp_path / "openminion",
        framework_root=tmp_path,
        data_root=tmp_path / "data",
        config_path=config,
        agent_id="openminion",
        workdir=tmp_path,
        session_id="session-a",
    )

    rebound = probe.for_session("room-review")

    assert rebound.session_id == "room-review"
    assert rebound.data_root == probe.data_root
    assert rebound.environment()["OPENMINION_HOME"].endswith("room-review")
    assert rebound.workdir == probe.workdir
