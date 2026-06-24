"""Console channel output formatting."""

from datetime import datetime, timezone
import os
import shutil
import sys
import textwrap

from openminion.base.constants import (
    BASE_COLOR_FORCE_FALSE_VALUES,
    BASE_COLOR_FORCE_TRUE_VALUES,
    NO_COLOR_ENV,
    OPENMINION_COLOR_ENV,
)
from .base import Channel
from openminion.base.types import Message


class ConsoleChannel(Channel):
    name = "console"

    def send(self, message: Message) -> None:
        timestamp = _chat_timestamp(message.timestamp)
        sender, content = _split_sender_and_content(message.body)
        plain_prefix = f"{timestamp} {sender}:"
        wrapped_lines = _wrap_content(content, prefix_width=len(plain_prefix) + 1)
        if not wrapped_lines:
            wrapped_lines = [""]

        if _terminal_supports_color():
            styled_prefix = (
                f"{_ansi(timestamp, '2')} "
                f"{_ansi(sender, _sender_style(sender))}"
                f"{_ansi(':', '2')}"
            )
        else:
            styled_prefix = plain_prefix

        print(f"{styled_prefix} {wrapped_lines[0]}".rstrip())
        continuation_indent = " " * (len(plain_prefix) + 1)
        for line in wrapped_lines[1:]:
            print(f"{continuation_indent}{line}")


def _split_sender_and_content(raw_body: str) -> tuple[str, str]:
    body = str(raw_body or "").strip()
    if ":" not in body:
        return "assistant", body

    left, right = body.split(":", 1)
    candidate = left.strip()
    if not candidate or " " in candidate or len(candidate) > 64:
        return "assistant", body
    return candidate, right.strip()


def _wrap_content(content: str, *, prefix_width: int = 0) -> list[str]:
    text = str(content or "").strip()
    if not text:
        return []
    width = max(
        24,
        min(120, shutil.get_terminal_size((100, 20)).columns - max(0, prefix_width)),
    )
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(line, width=width) or [""])
    return lines


def _terminal_supports_color() -> bool:
    no_color = str(os.environ.get(NO_COLOR_ENV, "")).strip()
    if no_color:
        return False
    forced = str(os.environ.get(OPENMINION_COLOR_ENV, "")).strip().lower()
    if forced in BASE_COLOR_FORCE_FALSE_VALUES:
        return False
    if forced in BASE_COLOR_FORCE_TRUE_VALUES:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _ansi(value: str, code: str) -> str:
    return f"\033[{code}m{value}\033[0m"


def _chat_timestamp(timestamp: datetime) -> str:
    return "[" + timestamp.astimezone(timezone.utc).strftime("%H:%M:%SZ") + "]"


def _sender_style(sender: str) -> str:
    normalized = str(sender or "").strip().lower()
    if normalized == "you":
        return "1;34"
    return "1;32"
