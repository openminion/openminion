from __future__ import annotations

import argparse
import os
from typing import Iterator

import pytest

from openminion.cli.ux.verbosity import (
    add_verbosity_flag,
    resolve_verbosity,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENMINION_VERBOSITY", raising=False)
    monkeypatch.delenv("OPENMINION_FOCUS_VERBOSITY", raising=False)
    yield


def _args(*, verbosity: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(verbosity=verbosity)


def test_default_returns_normal(clean_env: None) -> None:
    assert resolve_verbosity(_args()) == "normal"


def test_explicit_default_override(clean_env: None) -> None:
    assert resolve_verbosity(_args(), default="quiet") == "quiet"


def test_flag_quiet(clean_env: None) -> None:
    assert resolve_verbosity(_args(verbosity="quiet")) == "quiet"


def test_flag_verbose(clean_env: None) -> None:
    assert resolve_verbosity(_args(verbosity="verbose")) == "verbose"


def test_flag_normal_explicit(clean_env: None) -> None:
    assert resolve_verbosity(_args(verbosity="normal")) == "normal"


def test_env_quiet(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "quiet")
    assert resolve_verbosity(_args()) == "quiet"


def test_env_verbose(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "verbose")
    assert resolve_verbosity(_args()) == "verbose"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("QUIET", "quiet"),
        ("Verbose", "verbose"),
        ("Normal", "normal"),
        ("verbose ", "verbose"),
        (" quiet", "quiet"),
    ],
)
def test_env_case_insensitive(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: str,
) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", value)
    assert resolve_verbosity(_args()) == expected


def test_flag_beats_env(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "quiet")
    assert resolve_verbosity(_args(verbosity="verbose")) == "verbose"


def test_garbage_env_warns_and_falls_through(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "loud")
    result = resolve_verbosity(_args())
    assert result == "normal"
    err = capsys.readouterr().err
    assert "OPENMINION_VERBOSITY" in err
    assert "loud" in err


def test_empty_env_no_warning(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "")
    assert resolve_verbosity(_args()) == "normal"
    assert capsys.readouterr().err == ""


def test_legacy_env_resolves_with_deprecation_warning(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "verbose")
    result = resolve_verbosity(_args())
    assert result == "verbose"
    err = capsys.readouterr().err
    assert "OPENMINION_FOCUS_VERBOSITY is deprecated" in err
    assert "OPENMINION_VERBOSITY" in err


def test_canonical_env_beats_legacy(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "quiet")
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "verbose")
    result = resolve_verbosity(_args())
    assert result == "quiet"
    err = capsys.readouterr().err
    assert "deprecated" not in err


def test_legacy_env_garbage_falls_through(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "loud")
    assert resolve_verbosity(_args()) == "normal"


def test_flag_beats_legacy_env(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "verbose")
    result = resolve_verbosity(_args(verbosity="quiet"))
    assert result == "quiet"
    err = capsys.readouterr().err
    assert "deprecated" not in err


def test_add_verbosity_flag_accepts_choices() -> None:
    parser = argparse.ArgumentParser()
    add_verbosity_flag(parser)
    for choice in ("quiet", "normal", "verbose"):
        args = parser.parse_args(["--verbosity", choice])
        assert args.verbosity == choice


def test_add_verbosity_flag_default_none() -> None:
    parser = argparse.ArgumentParser()
    add_verbosity_flag(parser)
    args = parser.parse_args([])
    assert args.verbosity is None


def test_add_verbosity_flag_rejects_garbage() -> None:
    parser = argparse.ArgumentParser()
    add_verbosity_flag(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--verbosity", "loud"])


def test_only_ux_module_reads_canonical_verbosity_env() -> None:
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
            '"OPENMINION_VERBOSITY"',
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    hits = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    relative = sorted(os.path.relpath(h, str(src)) for h in hits)
    assert relative == ["cli/ux/verbosity.py"], relative


def test_helper_imports_only_minimal_deps() -> None:
    import openminion.cli.ux.verbosity as v

    src_text = open(v.__file__).read()
    assert "from openminion.cli.tui.terminal" not in src_text
    assert "import openminion.cli.tui.terminal" not in src_text
    assert "from openminion.cli.commands" not in src_text
