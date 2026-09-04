from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.cli.commands.interactive import _resolve_workspace_access
from openminion.cli.main import _run_no_handler
from openminion.cli.parser.base import build_parser


def test_explicit_workspace_and_added_directories_are_canonical(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    added = tmp_path / "shared"
    workspace.mkdir()
    added.mkdir()

    resolved, read_only, roots = _resolve_workspace_access(
        Namespace(dir=str(workspace), add_dir=[str(added), str(added)])
    )

    assert resolved == str(workspace.resolve())
    assert read_only is False
    assert roots == (str(added.resolve()),)


def test_implicit_git_workspace_is_trusted(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    with patch("subprocess.run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout="true\n")
        resolved, read_only, roots = _resolve_workspace_access(
            Namespace(dir=None, add_dir=[])
        )

    assert resolved == str(project)
    assert read_only is False
    assert roots == ()
    run.assert_called_once_with(
        ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("failure", ["nonzero", "missing"])
def test_implicit_non_git_or_missing_git_starts_read_only(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    with patch("subprocess.run") as run:
        if failure == "missing":
            run.side_effect = FileNotFoundError
        else:
            run.return_value = SimpleNamespace(returncode=1, stdout="")
        _, read_only, _ = _resolve_workspace_access(Namespace(dir=None, add_dir=[]))

    assert read_only is True


def test_implicit_home_stops_with_dir_remediation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with patch.object(Path, "home", return_value=tmp_path):
        with pytest.raises(ValueError, match="--dir PATH"):
            _resolve_workspace_access(Namespace(dir=None, add_dir=[]))


def test_missing_added_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="added directory does not exist"):
        _resolve_workspace_access(
            Namespace(dir=str(tmp_path), add_dir=[str(tmp_path / "missing")])
        )


def test_add_dir_is_rejected_before_piped_input_dispatch(monkeypatch) -> None:
    class _Input:
        @staticmethod
        def isatty() -> bool:
            return False

    class _Output:
        @staticmethod
        def isatty() -> bool:
            return False

    monkeypatch.setattr("sys.stdin", _Input())
    monkeypatch.setattr("sys.stdout", _Output())

    with pytest.raises(SystemExit) as exc_info:
        _run_no_handler(
            Namespace(add_dir=["/tmp/shared"]),
            build_parser(),
            "",
            "",
            "",
            "",
        )

    assert exc_info.value.code == 2
