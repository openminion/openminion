"""File backend search helpers."""

import re
from collections.abc import Callable

from openminion.modules.tool.errors import ToolRuntimeError


def compile_line_matcher(
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
) -> Callable[[str], bool]:
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error as exc:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"invalid regex: {exc}",
            ) from exc
        return lambda line: bool(pattern.search(line))

    needle = query if case_sensitive else query.lower()

    def _matches(line: str) -> bool:
        haystack = line if case_sensitive else line.lower()
        return needle in haystack

    return _matches


def search_snippet(
    lines: list[str],
    *,
    line_no: int,
    line: str,
    context_lines: int,
) -> str:
    if context_lines <= 0:
        return line[:200]
    before = lines[max(0, line_no - 1 - context_lines) : line_no - 1]
    after = lines[line_no : line_no + context_lines]
    return "\n".join(before + [line] + after)[:400]
