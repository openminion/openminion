from __future__ import annotations

import pytest


import json
from pathlib import Path

from tests.e2e.cli.focus.harness.probe import FocusProbe

pytestmark = pytest.mark.e2e


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
