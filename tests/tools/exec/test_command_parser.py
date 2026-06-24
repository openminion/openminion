from __future__ import annotations

import pytest

from openminion.tools.exec.command_parser import (
    CommandParseError,
    ParseResult,
    is_read_only_exec_command,
    parse_command,
)
from openminion.tools.exec.process import ShellFamily


def test_parse_command_returns_contract_shape_and_offsets() -> None:
    command = "  printf 'hello world'  "
    parsed = parse_command(command)

    assert isinstance(parsed, ParseResult)
    assert len(parsed.segments) == 1
    assert parsed.operators == ()
    segment = parsed.segments[0]
    assert segment.raw == "printf 'hello world'"
    assert segment.start == 2
    assert segment.end == len(command) - 2
    assert segment.argv == ("printf", "hello world")


def test_parse_command_keeps_operator_length_invariant() -> None:
    parsed = parse_command("echo one && echo two || echo three")
    assert len(parsed.operators) == len(parsed.segments) - 1


def test_parse_command_rejects_empty_input() -> None:
    with pytest.raises(CommandParseError) as exc_info:
        parse_command("   ")
    assert exc_info.value.code == "invalid_syntax"


def test_parse_command_handles_quoted_separators_as_literals() -> None:
    command = 'python3 -c "import secrets; print(secrets.token_hex(8) | 1)"'
    parsed = parse_command(command)

    assert len(parsed.segments) == 1
    assert parsed.operators == ()
    assert parsed.segments[0].argv == (
        "python3",
        "-c",
        "import secrets; print(secrets.token_hex(8) | 1)",
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r"echo one\;two", ("echo", "one;two")),
        (r"echo one\|two", ("echo", "one|two")),
        (r"echo one\&two", ("echo", "one&two")),
    ],
)
def test_parse_command_handles_escaped_separators(
    command: str, expected: tuple[str, ...]
) -> None:
    parsed = parse_command(command)
    assert len(parsed.segments) == 1
    assert parsed.operators == ()
    assert parsed.segments[0].argv == expected


def test_parse_command_tracks_unquoted_operator_boundaries() -> None:
    parsed = parse_command("echo one && echo two || echo three; echo four | wc -l")
    assert parsed.operators == ("&&", "||", ";", "|")
    assert [segment.argv[0] for segment in parsed.segments] == [
        "echo",
        "echo",
        "echo",
        "echo",
        "wc",
    ]


def test_parse_command_rejects_unquoted_redirection() -> None:
    with pytest.raises(CommandParseError) as exc_info:
        parse_command("echo hi > out.txt")
    assert exc_info.value.code == "unsupported_redirection"
    assert exc_info.value.position == 8


@pytest.mark.parametrize(
    ("command", "expected_position"),
    [
        ("echo hi &", 8),
        ("echo hi\npwd", 7),
    ],
)
def test_parse_command_rejects_unquoted_unsupported_syntax(
    command: str,
    expected_position: int,
) -> None:
    with pytest.raises(CommandParseError) as exc_info:
        parse_command(command)
    assert exc_info.value.code == "unsupported_syntax"
    assert exc_info.value.position == expected_position


@pytest.mark.parametrize(
    ("command", "expected_position"),
    [
        ("; ls", 0),
        ("ls ;; pwd", 4),
        ("ls &&", 3),
        ("|| true", 0),
    ],
)
def test_parse_command_rejects_empty_segments(
    command: str,
    expected_position: int,
) -> None:
    with pytest.raises(CommandParseError) as exc_info:
        parse_command(command)
    assert exc_info.value.code == "empty_segment"
    assert exc_info.value.position == expected_position


def test_parse_command_reports_unmatched_quote_position() -> None:
    command = 'echo "unterminated'
    with pytest.raises(CommandParseError) as exc_info:
        parse_command(command)
    assert exc_info.value.code == "unmatched_quote"
    assert exc_info.value.position == command.index('"')


def test_parse_command_dispatcher_preserves_posix_path() -> None:
    parsed = parse_command("echo hello", shell_family=ShellFamily.POSIX)
    assert parsed.segments[0].argv == ("echo", "hello")
    assert parsed.operators == ()


@pytest.mark.parametrize(
    "shell_family",
    [ShellFamily.POWERSHELL, ShellFamily.CMD],
)
def test_parse_command_dispatcher_rejects_control_operators_for_windows_subset(
    shell_family: ShellFamily,
) -> None:
    with pytest.raises(CommandParseError) as exc_info:
        parse_command("echo hi && whoami", shell_family=shell_family)
    assert exc_info.value.code == "unsupported_syntax"


def test_parse_command_dispatcher_denies_unknown_shell_family() -> None:
    with pytest.raises(CommandParseError) as exc_info:
        parse_command("echo hello", shell_family=ShellFamily.UNKNOWN)
    assert exc_info.value.code == "unsupported_shell"


def test_is_read_only_exec_command_uses_parser_and_fails_closed() -> None:
    assert is_read_only_exec_command("ls -la")
    assert not is_read_only_exec_command("touch /tmp/demo.txt")
    assert not is_read_only_exec_command("echo 'unterminated")


def test_is_read_only_exec_command_fails_closed_for_non_posix_shell_family() -> None:
    assert not is_read_only_exec_command(
        "ls -la",
        shell_family=ShellFamily.POWERSHELL,
    )
    assert not is_read_only_exec_command(
        "ls -la",
        shell_family=ShellFamily.CMD,
    )
