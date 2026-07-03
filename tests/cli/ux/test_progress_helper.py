from __future__ import annotations

import argparse
import os
from types import SimpleNamespace
from typing import Iterator

import pytest

from openminion.cli.ux.verbosity import (
    add_progress_flag,
    resolve_progress,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENMINION_PROGRESS", raising=False)
    monkeypatch.delenv("OPENMINION_FOCUS_PLAIN_SPINNER", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    yield


@pytest.fixture
def force_tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)
    yield


@pytest.fixture
def force_piped(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)
    yield


def _args(
    *,
    progress: str | None = None,
    no_progress: bool = False,
    no_activity_indicator: bool = False,
    plain_spinner: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        progress=progress,
        no_progress=no_progress,
        no_activity_indicator=no_activity_indicator,
        plain_spinner=plain_spinner,
    )


def test_auto_detect_tty_returns_full(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args()) == "full"


def test_auto_detect_piped_returns_off(clean_env: None, force_piped: None) -> None:
    assert resolve_progress(_args()) == "off"


def test_explicit_default_overrides_auto_detect(
    clean_env: None, force_piped: None
) -> None:
    assert resolve_progress(_args(), default="minimal") == "minimal"


def test_flag_full(clean_env: None, force_piped: None) -> None:
    assert resolve_progress(_args(progress="full")) == "full"


def test_flag_minimal(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args(progress="minimal")) == "minimal"


def test_flag_off(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args(progress="off")) == "off"


def test_no_progress_alias_returns_off(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args(no_progress=True)) == "off"


def test_no_activity_indicator_alias_returns_off(
    clean_env: None, force_tty: None
) -> None:
    assert resolve_progress(_args(no_activity_indicator=True)) == "off"


def test_plain_spinner_alias_returns_minimal(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args(plain_spinner=True)) == "minimal"


def test_canonical_flag_beats_legacy_alias(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args(progress="full", no_progress=True)) == "full"


def test_canonical_flag_beats_plain_spinner(clean_env: None, force_tty: None) -> None:
    assert resolve_progress(_args(progress="off", plain_spinner=True)) == "off"


def test_env_full(
    clean_env: None, force_piped: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "full")
    assert resolve_progress(_args()) == "full"


def test_env_minimal(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "minimal")
    assert resolve_progress(_args()) == "minimal"


def test_env_off(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "off")
    assert resolve_progress(_args()) == "off"


def test_env_case_insensitive(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "MINIMAL")
    assert resolve_progress(_args()) == "minimal"


def test_garbage_env_falls_through_with_warning(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "loud")
    result = resolve_progress(_args())
    assert result == "full"  # auto-detect (TTY) kicks in
    err = capsys.readouterr().err
    assert "OPENMINION_PROGRESS" in err
    assert "loud" in err


def test_legacy_plain_spinner_env_returns_minimal_with_warning(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", "1")
    result = resolve_progress(_args())
    assert result == "minimal"
    err = capsys.readouterr().err
    assert "OPENMINION_FOCUS_PLAIN_SPINNER is deprecated" in err
    assert "OPENMINION_PROGRESS=minimal" in err


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_legacy_plain_spinner_truthy_variants(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", value)
    assert resolve_progress(_args()) == "minimal"


def test_canonical_env_beats_legacy(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "off")
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", "1")
    result = resolve_progress(_args())
    assert result == "off"
    assert "deprecated" not in capsys.readouterr().err


def test_no_color_returns_minimal(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_progress(_args()) == "minimal"


def test_no_color_any_value(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "anything")
    assert resolve_progress(_args()) == "minimal"


def test_no_color_empty_no_effect(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "")
    assert resolve_progress(_args()) == "full"  # auto-detect


def test_auto_detect_handles_non_callable_isatty(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v.sys, "stdin", SimpleNamespace(isatty=False))
    monkeypatch.setattr(v.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    assert resolve_progress(_args()) == "off"


def test_canonical_env_beats_no_color(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "full")
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_progress(_args()) == "full"


def test_add_progress_flag_accepts_choices() -> None:
    parser = argparse.ArgumentParser()
    add_progress_flag(parser)
    for choice in ("full", "minimal", "off"):
        args = parser.parse_args(["--progress", choice])
        assert args.progress == choice


def test_add_progress_flag_default_none() -> None:
    parser = argparse.ArgumentParser()
    add_progress_flag(parser)
    args = parser.parse_args([])
    assert args.progress is None


def test_add_progress_flag_rejects_garbage() -> None:
    parser = argparse.ArgumentParser()
    add_progress_flag(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--progress", "loud"])


def test_add_progress_flag_with_aliases() -> None:
    parser = argparse.ArgumentParser()
    add_progress_flag(parser, include_aliases=True)
    args = parser.parse_args(["--no-progress"])
    assert args.no_progress is True
    args = parser.parse_args(["--plain-spinner"])
    assert args.plain_spinner is True
    args = parser.parse_args(["--no-activity-indicator"])
    assert args.no_activity_indicator is True


def test_add_progress_flag_without_aliases() -> None:
    parser = argparse.ArgumentParser()
    add_progress_flag(parser, include_aliases=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--no-progress"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--plain-spinner"])


def test_only_ux_module_reads_progress_env() -> None:
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
            '"OPENMINION_PROGRESS"',
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    hits = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    relative = sorted(os.path.relpath(h, str(src)) for h in hits)
    assert relative == ["cli/ux/verbosity.py"], relative
