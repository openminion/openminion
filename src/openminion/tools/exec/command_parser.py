import shlex
from dataclasses import dataclass

from .process import ShellFamily


@dataclass(frozen=True)
class CommandSegment:
    raw: str
    argv: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class ParseResult:
    segments: tuple[CommandSegment, ...]
    operators: tuple[str, ...]


class CommandParseError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        position: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.position = position


_READ_ONLY_EXECUTABLES = frozenset(
    {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "find",
        "grep",
        "wc",
        "stat",
        "tree",
    }
)


def _segment_bounds(command: str) -> tuple[int, int]:
    start = 0
    end = len(command)
    while start < end and command[start].isspace():
        start += 1
    while end > start and command[end - 1].isspace():
        end -= 1
    return start, end


def _parse_segment(
    command: str,
    *,
    start: int,
    end: int,
) -> CommandSegment:
    segment_text = command[start:end]
    try:
        argv = tuple(shlex.split(segment_text))
    except ValueError as exc:
        message = str(exc)
        code = "unmatched_quote" if "quotation" in message.lower() else "invalid_syntax"
        raise CommandParseError(
            code=code,
            message=message,
            position=start,
        ) from exc

    if not argv:
        raise CommandParseError(
            code="invalid_syntax",
            message="command must include an executable",
            position=start,
        )
    return CommandSegment(
        raw=segment_text,
        argv=argv,
        start=start,
        end=end,
    )


def _parse_command_posix(command: str) -> ParseResult:
    raw = str(command)
    command_start, command_end = _segment_bounds(raw)
    if command_start >= command_end:
        raise CommandParseError(
            code="invalid_syntax",
            message="command must not be empty",
            position=None,
        )

    segments: list[CommandSegment] = []
    operators: list[str] = []
    segment_start = command_start
    last_separator_position: int | None = None
    quote_mode: str | None = None
    open_quote_position: int | None = None
    idx = command_start

    while idx < command_end:
        ch = raw[idx]
        if quote_mode is None:
            if ch == "\\":
                if idx + 1 < command_end and raw[idx + 1] in {";", "|", "&"}:
                    idx += 2
                    continue
                idx += 1
                continue
            if ch == "'":
                quote_mode = "single"
                open_quote_position = idx
                idx += 1
                continue
            if ch == '"':
                quote_mode = "double"
                open_quote_position = idx
                idx += 1
                continue
            if ch in {"<", ">"}:
                raise CommandParseError(
                    code="unsupported_redirection",
                    message="unsupported command syntax: redirections are not supported",
                    position=idx,
                )
            if ch == "\n":
                raise CommandParseError(
                    code="unsupported_syntax",
                    message="unsupported command syntax: newline is not supported",
                    position=idx,
                )

            operator: str | None = None
            operator_end = idx + 1
            if ch == "&":
                if idx + 1 < command_end and raw[idx + 1] == "&":
                    operator = "&&"
                    operator_end = idx + 2
                else:
                    raise CommandParseError(
                        code="unsupported_syntax",
                        message="unsupported command syntax: '&' is not supported",
                        position=idx,
                    )
            elif ch == "|":
                if idx + 1 < command_end and raw[idx + 1] == "|":
                    operator = "||"
                    operator_end = idx + 2
                else:
                    operator = "|"
            elif ch == ";":
                operator = ";"

            if operator is not None:
                part_start, part_end = _segment_bounds(raw[segment_start:idx])
                part_start += segment_start
                part_end += segment_start
                if part_start >= part_end:
                    raise CommandParseError(
                        code="empty_segment",
                        message="unsupported command syntax: empty command segment",
                        position=idx,
                    )
                segments.append(_parse_segment(raw, start=part_start, end=part_end))
                operators.append(operator)
                last_separator_position = idx
                segment_start = operator_end
                idx = operator_end
                continue
            idx += 1
            continue

        if quote_mode == "single":
            if ch == "'":
                quote_mode = None
                open_quote_position = None
            idx += 1
            continue

        if ch == "\\":
            if idx + 1 < command_end:
                idx += 2
            else:
                idx += 1
            continue
        if ch == '"':
            quote_mode = None
            open_quote_position = None
        idx += 1

    if quote_mode is not None:
        raise CommandParseError(
            code="unmatched_quote",
            message="No closing quotation",
            position=open_quote_position,
        )

    tail_start, tail_end = _segment_bounds(raw[segment_start:command_end])
    tail_start += segment_start
    tail_end += segment_start
    if tail_start >= tail_end:
        raise CommandParseError(
            code="empty_segment",
            message="unsupported command syntax: empty command segment",
            position=last_separator_position,
        )
    segments.append(_parse_segment(raw, start=tail_start, end=tail_end))
    return ParseResult(segments=tuple(segments), operators=tuple(operators))


def _parse_command_portable_windows_subset(
    command: str,
    *,
    shell_family: ShellFamily,
) -> ParseResult:
    raw = str(command)
    start, end = _segment_bounds(raw)
    if start >= end:
        raise CommandParseError(
            code="invalid_syntax",
            message="command must not be empty",
            position=None,
        )

    for idx in range(start, end):
        ch = raw[idx]
        if ch in {"<", ">"}:
            raise CommandParseError(
                code="unsupported_redirection",
                message="unsupported command syntax: redirections are not supported",
                position=idx,
            )
        if ch == "\n":
            raise CommandParseError(
                code="unsupported_syntax",
                message="unsupported command syntax: newline is not supported",
                position=idx,
            )
        if ch in {";", "|", "&"}:
            raise CommandParseError(
                code="unsupported_syntax",
                message=(
                    f"unsupported command syntax for {shell_family.value} "
                    "portable subset"
                ),
                position=idx,
            )

    segment_text = raw[start:end]
    try:
        argv = tuple(shlex.split(segment_text, posix=False))
    except ValueError as exc:
        raise CommandParseError(
            code="unmatched_quote",
            message=str(exc),
            position=start,
        ) from exc
    if not argv:
        raise CommandParseError(
            code="invalid_syntax",
            message="command must include an executable",
            position=start,
        )
    segment = CommandSegment(
        raw=segment_text,
        argv=argv,
        start=start,
        end=end,
    )
    return ParseResult(segments=(segment,), operators=())


def parse_command(
    command: str,
    *,
    shell_family: ShellFamily = ShellFamily.POSIX,
) -> ParseResult:
    if shell_family == ShellFamily.POSIX:
        return _parse_command_posix(command)

    if shell_family in {ShellFamily.POWERSHELL, ShellFamily.CMD}:
        return _parse_command_portable_windows_subset(
            command,
            shell_family=shell_family,
        )

    raise CommandParseError(
        code="unsupported_shell",
        message="unsupported shell family",
        position=None,
    )


def is_read_only_exec_command(
    command: str,
    *,
    shell_family: ShellFamily = ShellFamily.POSIX,
) -> bool:
    if shell_family != ShellFamily.POSIX:
        return False
    try:
        parsed = parse_command(command, shell_family=shell_family)
    except CommandParseError:
        return False
    if len(parsed.segments) != 1 or parsed.operators:
        return False
    executable = str(parsed.segments[0].argv[0]).strip().lower()
    return executable in _READ_ONLY_EXECUTABLES


__all__ = [
    "CommandParseError",
    "CommandSegment",
    "ParseResult",
    "is_read_only_exec_command",
    "parse_command",
]
