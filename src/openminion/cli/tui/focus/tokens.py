from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AtToken:
    """Active ``@`` mention span at the current cursor."""

    text: str
    start: int
    end: int

    @property
    def query(self) -> str:
        """Return the mention text without the leading ``@``."""
        return self.text[1:] if self.text.startswith("@") else self.text


def active_at_token(text: str, cursor: int) -> AtToken | None:
    """Return the active `@`-token at ``cursor`` in ``text``, or None."""
    if not text:
        return None
    cursor_clamped = max(0, min(cursor, len(text)))
    if cursor_clamped == 0:
        return None

    start = cursor_clamped
    while start > 0 and not text[start - 1].isspace():
        start -= 1

    if start >= cursor_clamped:
        return None

    if text[start] != "@":
        return None

    return AtToken(
        text=text[start:cursor_clamped],
        start=start,
        end=cursor_clamped,
    )


def cursor_offset_for_text_area(text: str, line: int, col: int) -> int:
    """Convert a ``TextArea.cursor_location`` to a string offset."""
    if not text:
        return 0
    lines = text.split("\n")
    line_clamped = max(0, min(line, len(lines) - 1))
    offset = 0
    for i in range(line_clamped):
        offset += len(lines[i]) + 1
    col_clamped = max(0, min(col, len(lines[line_clamped])))
    return offset + col_clamped


__all__ = ["AtToken", "active_at_token", "cursor_offset_for_text_area"]
