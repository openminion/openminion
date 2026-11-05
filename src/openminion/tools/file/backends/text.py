import hashlib
import re
from collections.abc import Iterable
from typing import cast

from openminion.modules.tool.contracts.schemas import ErrorCode
from openminion.modules.tool.errors import ToolRuntimeError

from .protocol import EditOperation, RangeResult


def encode_text(content: str) -> bytes:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "content is not valid UTF-8"
        ) from exc


def edit_content(content: str, operations: list[EditOperation]) -> str:
    for operation in operations:
        old, new = operation.old_text, operation.new_text
        count = content.count(old)
        if count == 0:
            raise ToolRuntimeError("NOT_FOUND", f"anchor not found: {old[:60]!r}")
        if count > 1:
            raise ToolRuntimeError(
                cast(ErrorCode, "AMBIGUOUS"),
                f"anchor matches {count} times: {old[:60]!r}",
            )
        start = content.index(old)
        before, after = content[:start], content[start + len(old) :]
        adjacent = (
            re.search(r"(\r\n|\r|\n)$", before)
            if operation.op == "insert_before"
            else re.match(r"\r\n|\r|\n", after)
        )
        newline = adjacent or re.search(r"\r\n|\r|\n", content)
        separator = newline.group() if newline else "\n"
        if operation.op == "replace":
            replacement = new
        elif operation.op == "insert_before":
            replacement = new + separator + old
        elif operation.op == "insert_after":
            replacement = old + separator + new
        else:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"unknown edit operation: {operation.op}"
            )
        content = before + replacement + after
    encode_text(content)
    return content


def select_range(
    lines: Iterable[str], *, start_line: int, end_line: int, max_chars: int
) -> RangeResult:
    selected: list[str] = []
    retained = 0
    total = 0
    truncated = False
    for total, line in enumerate(lines, start=1):
        if start_line <= total <= end_line:
            numbered = (
                "\n" if total > start_line else ""
            ) + f"{total}: {line.rstrip(chr(10) + chr(13))}"
            available = max_chars - retained
            if available:
                selected.append(numbered[:available])
            retained += min(available, len(numbered))
            truncated |= len(numbered) > available
    if total and start_line > total:
        raise ToolRuntimeError("INVALID_ARGUMENT", "start_line beyond file length")
    return RangeResult(
        "".join(selected),
        start_line if total else 1,
        min(end_line, total),
        total,
        truncated,
    )


def check_replacement_precondition(content: str, expected_digest: str | None) -> None:
    if (
        expected_digest is not None
        and hashlib.sha256(encode_text(content)).hexdigest() != expected_digest
    ):
        raise ToolRuntimeError(
            "PRECONDITION_FAILED", "file content changed since readback"
        )
