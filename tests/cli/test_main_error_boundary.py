from __future__ import annotations

import json
import sqlite3

import pytest

from openminion.base.config import ConfigError
from openminion.cli import main as cli_main


def _parser_with_handler(
    monkeypatch, handler, *, json_output: bool = False, verbosity: str = "normal"
):
    real = cli_main.build_parser()

    class _Parser:
        def parse_args(self, argv=None):
            args = real.parse_args(argv)
            args.handler = handler
            args.needs_app = False
            args.json = json_output
            args.verbosity = verbosity
            return args

        def error(self, message):
            raise SystemExit(message)

        def exit(self, status=0, message=None):
            if message:
                print(message, end="", file=__import__("sys").stderr)
            raise SystemExit(status)

        def print_help(self):
            pass

    monkeypatch.setattr(cli_main, "build_parser", lambda: _Parser())
    return _Parser()


def test_unexpected_exception_becomes_structured_error(monkeypatch, capsys) -> None:
    def handler(_args):
        raise ValueError("database disk image is malformed")

    _parser_with_handler(monkeypatch, handler)

    rc = cli_main.main(["version"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "internal error" in captured.err
    assert "ValueError: database disk image is malformed" in captured.err
    assert "--verbosity verbose" in captured.err
    assert "Traceback" not in captured.err
    # One line on stderr: the hint is part of it, not a second message.
    assert captured.err.count("\n") == 1
    assert captured.out == ""


def test_unexpected_exception_emits_json_when_requested(monkeypatch, capsys) -> None:
    def handler(_args):
        raise sqlite3.DatabaseError("database disk image is malformed")

    _parser_with_handler(monkeypatch, handler, json_output=True)

    rc = cli_main.main(["version"])

    captured = capsys.readouterr()
    assert rc == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "malformed" in payload["error"]["message"]
    assert "Traceback" not in captured.err


def test_keyboard_interrupt_exits_130_without_traceback(monkeypatch, capsys) -> None:
    def handler(_args):
        raise KeyboardInterrupt

    _parser_with_handler(monkeypatch, handler)

    rc = cli_main.main(["version"])

    captured = capsys.readouterr()
    assert rc == 130
    assert "cancelled" in captured.err
    assert "Traceback" not in captured.err
    assert "KeyboardInterrupt" not in captured.err


def test_verbose_verbosity_still_raises_for_traceback(monkeypatch) -> None:
    def handler(_args):
        raise ValueError("boom")

    _parser_with_handler(monkeypatch, handler, verbosity="verbose")

    with pytest.raises(ValueError, match="boom"):
        cli_main.main(["version"])


def test_config_errors_keep_exit_code_two(monkeypatch, capsys) -> None:
    def handler(_args):
        raise ConfigError("config file not found: /tmp/agents.json")

    _parser_with_handler(monkeypatch, handler)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main(["version"])

    assert excinfo.value.code == 2
    assert "config file not found" in capsys.readouterr().err


def test_abort_on_sigint_prints_and_exits_130(monkeypatch, capsys) -> None:
    codes = []
    monkeypatch.setattr(cli_main.os, "_exit", lambda code: codes.append(code))

    cli_main._abort_on_signal(cli_main.signal.SIGINT, None)

    assert codes == [130]
    assert "cancelled" in capsys.readouterr().err


def test_abort_on_sigterm_exits_143(monkeypatch, capsys) -> None:
    codes = []
    monkeypatch.setattr(cli_main.os, "_exit", lambda code: codes.append(code))

    cli_main._abort_on_signal(cli_main.signal.SIGTERM, None)

    assert codes == [143]
    assert "terminated" in capsys.readouterr().err


def test_cancel_on_signal_installs_and_restores_handlers() -> None:
    before = cli_main.signal.getsignal(cli_main.signal.SIGINT)

    with cli_main._cancel_on_signal(True):
        during = cli_main.signal.getsignal(cli_main.signal.SIGINT)
        assert during is cli_main._abort_on_signal

    assert cli_main.signal.getsignal(cli_main.signal.SIGINT) is before


def test_non_owning_invocation_leaves_the_host_handler_alone() -> None:
    """An embedded caller keeps its own SIGINT/SIGTERM dispositions."""
    host = lambda *args: None  # noqa: E731
    before_int = cli_main.signal.signal(cli_main.signal.SIGINT, host)
    before_term = cli_main.signal.signal(cli_main.signal.SIGTERM, host)
    try:
        # An imported main() passes owns_process=False, so the host keeps both handlers.
        with cli_main._cancel_on_signal(False):
            assert cli_main.signal.getsignal(cli_main.signal.SIGINT) is host
            assert cli_main.signal.getsignal(cli_main.signal.SIGTERM) is host
        with cli_main._cancel_on_signal(True):
            assert cli_main.signal.getsignal(cli_main.signal.SIGINT) is (
                cli_main._abort_on_signal
            )
    finally:
        cli_main.signal.signal(cli_main.signal.SIGINT, before_int)
        cli_main.signal.signal(cli_main.signal.SIGTERM, before_term)


def test_env_verbosity_selects_the_traceback(monkeypatch, capsys) -> None:
    """The documented env source counts as asking for a traceback, not just the flag."""

    def handler(_args):
        raise ValueError("boom")

    _parser_with_handler(monkeypatch, handler, verbosity="")
    monkeypatch.setenv("OPENMINION_VERBOSITY", "verbose")

    with pytest.raises(ValueError):
        cli_main.main(["version"])
    assert "internal error" not in capsys.readouterr().err


def test_multiline_message_is_rendered_on_one_line(monkeypatch, capsys) -> None:
    """The plain renderer collapses embedded newlines; JSON keeps them."""

    def handler(_args):
        raise ValueError("first line\nsecond line")

    _parser_with_handler(monkeypatch, handler)
    assert cli_main.main(["version"]) == 1
    plain = capsys.readouterr().err
    assert plain.count("\n") == 1
    assert "first line second line" in plain

    _parser_with_handler(monkeypatch, handler, json_output=True)
    assert cli_main.main(["version"]) == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["error"]["message"] == "ValueError: first line\nsecond line"
