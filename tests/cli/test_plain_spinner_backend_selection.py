from __future__ import annotations

import argparse
import os
from typing import Iterator

import pytest

from openminion.cli.commands.focus import _resolve_plain_spinner


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENMINION_FOCUS_PLAIN_SPINNER", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    yield


def _args(*, plain_spinner: bool = False) -> argparse.Namespace:
    return argparse.Namespace(plain_spinner=plain_spinner)


def test_default_returns_false(clean_env: None) -> None:
    assert _resolve_plain_spinner(_args()) is False


def test_flag_returns_true(clean_env: None) -> None:
    assert _resolve_plain_spinner(_args(plain_spinner=True)) is True


def test_env_truthy_returns_true(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", "1")
    assert _resolve_plain_spinner(_args()) is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "True"])
def test_env_truthy_variants_return_true(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", value)
    assert _resolve_plain_spinner(_args()) is True


def test_no_color_set_returns_true(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert _resolve_plain_spinner(_args()) is True


def test_no_color_any_value_returns_true(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "anything")
    assert _resolve_plain_spinner(_args()) is True


def test_no_color_empty_returns_false(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "")
    assert _resolve_plain_spinner(_args()) is False


def test_flag_and_env_both_set_returns_true(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", "1")
    assert _resolve_plain_spinner(_args(plain_spinner=True)) is True


def test_garbage_env_value_returns_false(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", "maybe")
    assert _resolve_plain_spinner(_args()) is False


def test_env_value_zero_returns_false(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_FOCUS_PLAIN_SPINNER", "0")
    assert _resolve_plain_spinner(_args()) is False


def test_no_color_takes_effect_when_explicit_env_unset(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENMINION_FOCUS_PLAIN_SPINNER", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert _resolve_plain_spinner(_args()) is True


def test_argparse_flag_registered() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args(["focus", "--plain-spinner"])
    assert getattr(args, "plain_spinner", False) is True


def test_argparse_flag_default_false() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args(["focus"])
    assert getattr(args, "plain_spinner", False) is False


def test_run_terminal_focus_accepts_plain_spinner_kwarg() -> None:
    import inspect

    from openminion.cli.tui.terminal.shell import run_terminal_focus

    sig = inspect.signature(run_terminal_focus)
    assert "plain_spinner" in sig.parameters


def test_terminal_response_time_env_default_and_opt_out() -> None:
    from openminion.cli.tui.terminal.shell import _show_response_time_enabled

    assert _show_response_time_enabled({}) is True
    assert _show_response_time_enabled({"OPENMINION_SHOW_RESPONSE_TIME": "0"}) is False


def test_terminal_transcript_accepts_plain_spinner_kwarg() -> None:
    import inspect

    from openminion.cli.tui.terminal.transcript import TerminalTranscript

    sig = inspect.signature(TerminalTranscript.__init__)
    assert "plain_spinner" in sig.parameters


def test_plain_spinner_propagates_to_turn_handle(
    clean_env: None,
) -> None:
    import io

    from rich.console import Console

    from openminion.cli.tui.terminal.transcript import TerminalTranscript

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        force_interactive=False,
        width=80,
        color_system="truecolor",
    )
    t = TerminalTranscript(console, plain_spinner=True)
    handle = t.begin_turn(role="assistant")
    assert handle._plain is True  # type: ignore[attr-defined]
    handle.complete(final_text="done")


def test_plain_spinner_default_propagates_false(clean_env: None) -> None:
    import io

    from rich.console import Console

    from openminion.cli.tui.terminal.transcript import TerminalTranscript

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        force_interactive=False,
        width=80,
        color_system="truecolor",
    )
    t = TerminalTranscript(console)
    handle = t.begin_turn(role="assistant")
    assert handle._plain is False  # type: ignore[attr-defined]
    handle.complete(final_text="done")


def test_only_ux_module_reads_legacy_plain_spinner_env() -> None:
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
            '"OPENMINION_FOCUS_PLAIN_SPINNER"',
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    hits = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    relative = sorted(os.path.relpath(h, str(src)) for h in hits)
    assert relative == ["cli/ux/verbosity.py"], relative
