from __future__ import annotations

from typing import Iterator

import pytest

from openminion.cli.ux.verbosity import (
    resolve_progress,
    resolve_verbosity,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in (
        "OPENMINION_VERBOSITY",
        "OPENMINION_PROGRESS",
        "NO_COLOR",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _build_parser():
    from openminion.cli.parser.base import build_parser

    return build_parser()


# ── Flag registration parity ──────────────────────────────────────


def test_interactive_root_accepts_verbosity_and_progress() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--verbosity", "quiet", "--progress", "off"])
    assert args.verbosity == "quiet"
    assert args.progress == "off"


def test_gateway_accepts_verbosity_and_progress() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "gateway",
            "run",
            "--once",
            "--message",
            "hi",
            "--verbosity",
            "verbose",
            "--progress",
            "minimal",
        ]
    )
    assert args.verbosity == "verbose"
    assert args.progress == "minimal"


def test_run_accepts_verbosity_and_progress() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["run", "hello", "--verbosity", "normal", "--progress", "full"]
    )
    assert args.verbosity == "normal"
    assert args.progress == "full"


def test_agent_accepts_verbosity_and_progress() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["agent", "--message", "hi", "--verbosity", "quiet", "--progress", "off"]
    )
    assert args.verbosity == "quiet"
    assert args.progress == "off"


def test_interactive_root_accepts_plain_spinner_alias() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--plain-spinner"])
    assert args.plain_spinner is True


def test_run_accepts_plain_spinner_alias() -> None:
    parser = _build_parser()
    args = parser.parse_args(["run", "hi", "--plain-spinner"])
    assert args.plain_spinner is True


def test_agent_accepts_plain_spinner_alias() -> None:
    parser = _build_parser()
    args = parser.parse_args(["agent", "--message", "hi", "--plain-spinner"])
    assert args.plain_spinner is True


def test_gateway_accepts_no_progress_alias() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["gateway", "run", "--once", "--message", "hi", "--no-progress"]
    )
    assert args.no_progress is True


def test_run_accepts_no_progress_alias() -> None:
    parser = _build_parser()
    args = parser.parse_args(["run", "hi", "--no-progress"])
    assert args.no_progress is True


# ── Resolution parity ────────────────────────────────────────────


def test_resolve_verbosity_consistent_across_surfaces(clean_env: None) -> None:
    parser = _build_parser()
    surfaces = [
        ["--verbosity", "quiet"],
        ["gateway", "run", "--once", "--message", "hi", "--verbosity", "quiet"],
        ["run", "hi", "--verbosity", "quiet"],
        ["agent", "--message", "hi", "--verbosity", "quiet"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_verbosity(args) == "quiet", invocation


def test_resolve_progress_consistent_across_surfaces(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)

    parser = _build_parser()
    surfaces = [
        ["--progress", "off"],
        ["gateway", "run", "--once", "--message", "hi", "--progress", "off"],
        ["run", "hi", "--progress", "off"],
        ["agent", "--message", "hi", "--progress", "off"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_progress(args) == "off", invocation


def test_resolve_progress_alias_consistent_across_surfaces(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)

    parser = _build_parser()
    # Every command-backed CUC surface registers `--no-progress` as an alias.
    surfaces = [
        ["gateway", "run", "--once", "--message", "hi", "--no-progress"],
        ["run", "hi", "--no-progress"],
        ["agent", "--message", "hi", "--no-progress"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_progress(args) == "off", invocation


def test_resolve_plain_spinner_alias_consistent(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)

    parser = _build_parser()
    surfaces = [
        ["--plain-spinner"],
        ["run", "hi", "--plain-spinner"],
        ["agent", "--message", "hi", "--plain-spinner"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_progress(args) == "minimal", invocation


def test_canonical_progress_beats_alias_on_every_surface(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)

    parser = _build_parser()
    surfaces = [
        [
            "gateway",
            "run",
            "--once",
            "--message",
            "hi",
            "--progress",
            "full",
            "--no-progress",
        ],
        ["run", "hi", "--progress", "full", "--no-progress"],
        ["agent", "--message", "hi", "--progress", "full", "--no-progress"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_progress(args) == "full", invocation


# ── Env precedence parity ────────────────────────────────────────


def test_env_verbosity_consistent_across_surfaces(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "verbose")
    parser = _build_parser()
    surfaces = [
        [],
        ["gateway", "run", "--once", "--message", "hi"],
        ["run", "hi"],
        ["agent", "--message", "hi"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_verbosity(args) == "verbose", invocation


def test_env_progress_consistent_across_surfaces(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_PROGRESS", "off")
    parser = _build_parser()
    surfaces = [
        [],
        ["gateway", "run", "--once", "--message", "hi"],
        ["run", "hi"],
        ["agent", "--message", "hi"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_progress(args) == "off", invocation


# ── Auto-detect parity (piped contexts default to off) ───────────


def test_auto_detect_off_on_pipe_across_surfaces(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)

    parser = _build_parser()
    surfaces = [
        [],
        ["gateway", "run", "--once", "--message", "hi"],
        ["run", "hi"],
        ["agent", "--message", "hi"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        # gateway has its own default-handling path (passes
        # default="full"), so explicitly check via the helper.
        # Since we patched stdin, the auto-detect returns "off".
        assert resolve_progress(args) == "off", invocation


def test_auto_detect_full_on_tty_across_surfaces(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openminion.cli.ux.verbosity as v

    monkeypatch.setattr(v, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(v, "_stdout_is_tty", lambda: True)

    parser = _build_parser()
    surfaces = [
        [],
        ["gateway", "run", "--once", "--message", "hi"],
        ["run", "hi"],
        ["agent", "--message", "hi"],
    ]
    for invocation in surfaces:
        args = parser.parse_args(invocation)
        assert resolve_progress(args) == "full", invocation


# ── Retired aliases are not in the consistency contract ─────────


def test_retired_chat_alias_is_rejected() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["chat", "--verbosity", "quiet"])
