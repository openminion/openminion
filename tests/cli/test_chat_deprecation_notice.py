from __future__ import annotations

import os
from typing import Iterator

import pytest

from openminion.cli.commands.chat import _NOTICE_TEXT
from openminion.cli.ux.deprecation import (
    deprecation_suppressed,
    print_deprecation_notice,
)

_CHAT_SUPPRESSION_ENV = "OPENMINION_CHAT_NO_DEPRECATION"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(_CHAT_SUPPRESSION_ENV, raising=False)
    yield


def test_notice_is_enabled_without_suppression(clean_env: None) -> None:
    assert deprecation_suppressed(_CHAT_SUPPRESSION_ENV) is False


def test_notice_is_suppressed_when_env_truthy(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_CHAT_SUPPRESSION_ENV, "1")
    assert deprecation_suppressed(_CHAT_SUPPRESSION_ENV) is True


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on", "True", "Yes", "ON"],
)
def test_truthy_suppression_variants(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(_CHAT_SUPPRESSION_ENV, value)
    assert deprecation_suppressed(_CHAT_SUPPRESSION_ENV) is True


@pytest.mark.parametrize("value", ["0", "", "maybe"])
def test_non_truthy_values_do_not_suppress(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(_CHAT_SUPPRESSION_ENV, value)
    assert deprecation_suppressed(_CHAT_SUPPRESSION_ENV) is False


def test_notice_text_has_expected_substrings(
    clean_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert print_deprecation_notice(
        _NOTICE_TEXT,
        suppression_env=_CHAT_SUPPRESSION_ENV,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    out = captured.err
    assert "compatibility alias" in out
    assert "openminion" in out
    assert "OPENMINION_CHAT_NO_DEPRECATION" in out


def test_notice_suppressed_by_env(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(_CHAT_SUPPRESSION_ENV, "1")
    assert not print_deprecation_notice(
        _NOTICE_TEXT,
        suppression_env=_CHAT_SUPPRESSION_ENV,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_helper_is_callable_multiple_times_without_state(
    clean_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    print_deprecation_notice(
        _NOTICE_TEXT,
        suppression_env=_CHAT_SUPPRESSION_ENV,
    )
    print_deprecation_notice(
        _NOTICE_TEXT,
        suppression_env=_CHAT_SUPPRESSION_ENV,
    )
    assert capsys.readouterr().err.count("compatibility alias") == 2


def test_run_chat_invokes_notice_helper_exactly_once() -> None:
    import inspect

    from openminion.cli.commands import chat as chat_cmd

    source = inspect.getsource(chat_cmd.run_chat)
    assert source.count("print_deprecation_notice(") == 1


def test_chat_command_is_the_only_surface_declaring_suppression_env() -> None:
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
            if (
                isinstance(node, ast.Constant)
                and node.value == _CHAT_SUPPRESSION_ENV
            ):
                found = True
                break
        if found:
            hits.append(str(path))
    relative = sorted(os.path.relpath(h, str(src)) for h in hits)
    assert relative == ["cli/commands/chat.py"], relative


def test_notice_text_constant_includes_charter_link() -> None:
    assert "bare `openminion`" in _NOTICE_TEXT
