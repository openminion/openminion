"""First-run missing-config guidance: scope, exit codes and non-matches."""

from __future__ import annotations

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _cli_env(home_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    source_path = str(PROJECT_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join([source_path, existing])
    )
    env["OPENMINION_HOME"] = str(home_root)
    env["OPENMINION_DATA_ROOT"] = str(home_root / ".openminion")
    env["OPENMINION_GENERATED_ROOT"] = str(home_root / ".openminion" / "runtime")
    return env


def _run_cli(args: list[str], *, home_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "openminion", *args],
        cwd=PROJECT_ROOT,
        env=_cli_env(home_root),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_format_cli_error_only_advises_when_the_config_is_absent() -> None:
    from openminion.cli.main import format_cli_error

    absent = RuntimeError("config file not found: /tmp/home/agents.json")
    guidance = format_cli_error(absent, missing_config=True)
    assert guidance.startswith("config file not found: /tmp/home/agents.json")
    assert "openminion config init --provider <name>" in guidance

    # The same message without an absent path (malformed config, permission failure,
    # any other runtime error) keeps its own text.
    for unrelated in (
        RuntimeError("config file not found elsewhere"),
        ValueError("config file not found: /tmp/x"),
        RuntimeError("Invalid JSON config at /tmp/x: Expecting value"),
    ):
        assert format_cli_error(unrelated) == str(unrelated)
        assert format_cli_error(unrelated, missing_config=False) == str(unrelated)


def test_absent_default_config_explains_the_next_step(tmp_path: Path) -> None:
    result = _run_cli(["run", "hi"], home_root=tmp_path / "home")

    assert result.returncode == 2
    assert "config file not found" in result.stderr
    assert "openminion config init --provider <name>" in result.stderr


def test_absent_explicit_config_explains_the_next_step(tmp_path: Path) -> None:
    absent = tmp_path / "absent.json"
    result = _run_cli(["--config", str(absent), "run", "hi"], home_root=tmp_path)

    assert result.returncode == 2
    assert str(absent) in result.stderr
    assert "openminion config init --provider <name>" in result.stderr


def test_malformed_config_keeps_its_own_error_without_guidance(tmp_path: Path) -> None:
    malformed = tmp_path / "config file not found.json"
    malformed.write_text("{ not json", encoding="utf-8")

    result = _run_cli(["--config", str(malformed), "run", "hi"], home_root=tmp_path)

    assert result.returncode == 2
    assert "Invalid JSON config" in result.stderr
    assert "openminion config init" not in result.stderr


@pytest.mark.parametrize("handler", ["list", "delete"])
def test_sessions_startup_error_advises_and_keeps_its_exit_code(
    tmp_path: Path, handler: str
) -> None:
    from openminion.cli.commands import sessions as sessions_command

    args = Namespace(
        agent=None,
        status=None,
        channel=None,
        limit=10,
        output_json=False,
        session_id="ghost",
        yes=True,
        config=str(tmp_path / "absent.json"),
        home_root=None,
        data_root=None,
    )

    run = (
        sessions_command.run_sessions_list
        if handler == "list"
        else sessions_command.run_sessions_delete
    )
    assert run(args) == 1


def test_sessions_unrelated_startup_error_has_no_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from openminion.cli.commands import sessions as sessions_command

    monkeypatch.setattr(
        sessions_command,
        "_load_session_storage",
        lambda _args: (_ for _ in ()).throw(RuntimeError("bad config")),
    )
    args = Namespace(
        agent=None,
        status=None,
        channel=None,
        limit=10,
        output_json=False,
        config=str(tmp_path / "absent.json"),
        home_root=None,
        data_root=None,
    )

    assert sessions_command.run_sessions_list(args) == 1
    err = capsys.readouterr().err
    assert "bad config" in err
    assert "openminion config init" not in err
