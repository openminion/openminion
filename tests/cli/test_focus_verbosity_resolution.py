from __future__ import annotations

import argparse

import pytest

from openminion.cli.commands.interactive import _resolve_focus_verbosity
from openminion.cli.parser.base import build_parser


def _args(*, verbosity: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(verbosity=verbosity)


def test_default_returns_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENMINION_VERBOSITY", raising=False)
    assert _resolve_focus_verbosity(_args()) == "normal"


def test_canonical_env_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "verbose")
    assert _resolve_focus_verbosity(_args()) == "verbose"


def test_flag_beats_canonical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "quiet")
    assert _resolve_focus_verbosity(_args(verbosity="verbose")) == "verbose"


@pytest.mark.parametrize("choice", ("quiet", "normal", "verbose"))
def test_root_interactive_flag_accepts_canonical_choices(choice: str) -> None:
    args = build_parser().parse_args(["--verbosity", choice])
    assert args.verbosity == choice


def test_root_interactive_flag_rejects_invalid_choice() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--verbosity", "loud"])
