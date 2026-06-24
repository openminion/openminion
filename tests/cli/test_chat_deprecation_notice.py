from __future__ import annotations

import io
import os
from typing import Iterator

import pytest

from openminion.cli.chat._deprecation import (
    print_deprecation_notice,
    should_print_notice,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("OPENMINION_CHAT_NO_DEPRECATION", raising=False)
    yield


@pytest.fixture
def force_tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import openminion.cli.chat._deprecation as dep

    monkeypatch.setattr(dep, "_stdout_is_tty", lambda: True)
    yield


@pytest.fixture
def force_non_tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import openminion.cli.chat._deprecation as dep

    monkeypatch.setattr(dep, "_stdout_is_tty", lambda: False)
    yield


def test_should_print_on_tty_with_no_env(clean_env: None, force_tty: None) -> None:
    assert should_print_notice() is True


def test_should_not_print_on_non_tty(clean_env: None, force_non_tty: None) -> None:
    assert should_print_notice() is False


def test_should_not_print_when_env_truthy(
    clean_env: None, force_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_CHAT_NO_DEPRECATION", "1")
    assert should_print_notice() is False


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on", "True", "Yes", "ON"],
)
def test_should_not_print_truthy_variants(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENMINION_CHAT_NO_DEPRECATION", value)
    assert should_print_notice() is False


@pytest.mark.parametrize("value", ["0", "", "maybe"])
def test_should_still_print_for_non_truthy_env_values(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENMINION_CHAT_NO_DEPRECATION", value)
    assert should_print_notice() is True


def test_non_tty_takes_precedence_over_env(
    clean_env: None, force_non_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENMINION_CHAT_NO_DEPRECATION", raising=False)
    assert should_print_notice() is False


def test_notice_text_has_expected_substrings(
    clean_env: None, force_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    print_deprecation_notice()
    out = capsys.readouterr().out
    assert "maintenance mode" in out
    assert "openminion focus" in out
    assert "OPENMINION_CHAT_NO_DEPRECATION" in out
    assert "chat migration guide" in out


def test_notice_suppressed_by_env(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENMINION_CHAT_NO_DEPRECATION", "1")
    print_deprecation_notice()
    assert capsys.readouterr().out == ""


def test_notice_suppressed_on_non_tty(
    clean_env: None,
    force_non_tty: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_deprecation_notice()
    assert capsys.readouterr().out == ""


def test_notice_with_rich_console(clean_env: None, force_tty: None) -> None:
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    print_deprecation_notice(console=console)
    out = buf.getvalue()
    assert "maintenance mode" in out
    assert "openminion focus" in out


def test_notice_with_rich_console_suppressed_by_env(
    clean_env: None,
    force_tty: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.console import Console

    monkeypatch.setenv("OPENMINION_CHAT_NO_DEPRECATION", "1")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    print_deprecation_notice(console=console)
    assert buf.getvalue() == ""


def test_helper_is_callable_multiple_times_without_state(
    clean_env: None, force_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    print_deprecation_notice()
    print_deprecation_notice()
    out = capsys.readouterr().out
    assert out.count("maintenance mode") == 2


def test_run_chat_invokes_notice_helper_exactly_once() -> None:
    import inspect

    from openminion.cli.commands import chat as chat_cmd

    source = inspect.getsource(chat_cmd.run_chat)
    assert source.count("print_deprecation_notice()") == 1


def test_only_deprecation_module_reads_chat_env() -> None:
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "openminion"
    hits = []
    for path in sorted(src.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get":
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and (
                first.value == "OPENMINION_CHAT_NO_DEPRECATION"
            ):
                found = True
                break
        if found:
            hits.append(str(path))
    relative = sorted(os.path.relpath(h, str(src)) for h in hits)
    assert relative == ["cli/chat/_deprecation.py"], relative


# ── Module surface sanity ────────────────────────────────────────


def test_deprecation_module_imports_cleanly() -> None:
    from openminion.cli.chat import _deprecation as dep

    assert callable(dep.print_deprecation_notice)
    assert callable(dep.should_print_notice)


def test_notice_text_constant_includes_charter_link() -> None:
    from openminion.cli.chat._deprecation import _NOTICE_TEXT

    assert "chat migration guide" in _NOTICE_TEXT
