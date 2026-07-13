from __future__ import annotations

import argparse
import os
from typing import Iterator

import pytest

from openminion.cli.commands.focus import _resolve_focus_verbosity


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENMINION_FOCUS_VERBOSITY", raising=False)
    yield


def _args(*, verbosity: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(verbosity=verbosity)


def test_default_returns_normal(clean_env: None) -> None:
    assert _resolve_focus_verbosity(_args()) == "normal"


def test_flag_quiet(clean_env: None) -> None:
    assert _resolve_focus_verbosity(_args(verbosity="quiet")) == "quiet"


def test_flag_verbose(clean_env: None) -> None:
    assert _resolve_focus_verbosity(_args(verbosity="verbose")) == "verbose"


def test_flag_normal_explicit(clean_env: None) -> None:
    assert _resolve_focus_verbosity(_args(verbosity="normal")) == "normal"


def test_env_quiet(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "quiet")
    assert _resolve_focus_verbosity(_args()) == "quiet"


def test_env_verbose(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "verbose")
    assert _resolve_focus_verbosity(_args()) == "verbose"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("QUIET", "quiet"),
        ("Quiet", "quiet"),
        ("VERBOSE", "verbose"),
        ("Normal", "normal"),
        ("verbose ", "verbose"),  # trailing whitespace
        (" quiet", "quiet"),  # leading whitespace
    ],
)
def test_env_case_insensitive(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: str,
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", value)
    assert _resolve_focus_verbosity(_args()) == expected


def test_flag_beats_env(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "quiet")
    assert _resolve_focus_verbosity(_args(verbosity="verbose")) == "verbose"


def test_garbage_env_falls_back_to_normal_with_warning(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "loud")
    result = _resolve_focus_verbosity(_args())
    assert result == "normal"
    err = capsys.readouterr().err
    assert "OPENMINION_FOCUS_VERBOSITY" in err
    assert "loud" in err
    assert "normal" in err
    # Valid values should be listed in the warning so the user
    # knows what to set instead.
    assert "quiet" in err
    assert "verbose" in err


def test_empty_env_no_warning(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "")
    result = _resolve_focus_verbosity(_args())
    assert result == "normal"
    # Empty string is "unset" semantics — no warning.
    assert capsys.readouterr().err == ""


def test_argparse_flag_choices_registered() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)

    for choice in ("quiet", "normal", "verbose"):
        args = parser.parse_args(["focus", "--verbosity", choice])
        assert args.verbosity == choice


def test_argparse_flag_default_none() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args(["focus"])
    assert args.verbosity is None


def test_argparse_invalid_choice_rejected() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    with pytest.raises(SystemExit):
        parser.parse_args(["focus", "--verbosity", "loud"])


def test_only_ux_module_reads_legacy_focus_verbosity_env() -> None:
    import pathlib
    import subprocess

    repo_root = pathlib.Path(__file__).resolve()
    while not (repo_root / "openminion").exists() and repo_root.parent != repo_root:
        repo_root = repo_root.parent
    src = repo_root / "openminion" / "src" / "openminion"
    proc = subprocess.run(
        [
            "grep",
            "-rln",
            "--include=*.py",
            '"OPENMINION_FOCUS_VERBOSITY"',
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    hits = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    relative = sorted(os.path.relpath(h, str(src)) for h in hits)
    assert relative == ["cli/ux/verbosity.py"], relative
