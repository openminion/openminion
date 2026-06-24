"""Tiny terminal UX helpers for human-readable repo scripts."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

_RESET = "\033[0m"
_CODES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}
_STATUS_STYLES = {
    "ok": ("bold", "green"),
    "fail": ("bold", "red"),
    "warn": ("bold", "yellow"),
    "info": ("bold", "cyan"),
    "advisory": ("bold", "magenta"),
}


def color_enabled(stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    force = os.environ.get("OPENMINION_COLOR", "").strip().lower()
    if force in {"1", "true", "yes", "on", "always"}:
        return True
    if force in {"0", "false", "no", "off"}:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def style(text: str, *tokens: str, stream: TextIO | None = None) -> str:
    if not color_enabled(stream):
        return text
    prefix = "".join(_CODES[token] for token in tokens if token in _CODES)
    return f"{prefix}{text}{_RESET}" if prefix else text


def status_tag(kind: str, *, stream: TextIO | None = None) -> str:
    normalized = kind.strip().lower()
    label = normalized.upper()
    tokens = _STATUS_STYLES.get(normalized, ("bold",))
    return style(f"[{label}]", *tokens, stream=stream)


def status_line(kind: str, message: str, *, stream: TextIO | None = None) -> str:
    return f"{status_tag(kind, stream=stream)} {message}"


def heading(title: str, *, stream: TextIO | None = None) -> str:
    return style(title, "bold", "cyan", stream=stream)


def section(title: str, *, kind: str = "info", stream: TextIO | None = None) -> str:
    return f"{status_tag(kind, stream=stream)} {style(title, 'bold', stream=stream)}"


def key_value(label: str, value: object, *, stream: TextIO | None = None) -> str:
    return f"{style(label, 'bold', stream=stream)}: {value}"


def item(text: str, *, prefix: str = "  - ") -> str:
    return f"{prefix}{text}"


def emit_grouped_path_findings(
    title: str,
    grouped_findings: Sequence[tuple[Path, int, str]],
    *,
    render_detail,
    ok_message: str,
    empty_message: str | None = None,
    report_stream: TextIO | None = None,
) -> None:
    report_stream = report_stream or sys.stdout
    print(heading(title, stream=report_stream), file=report_stream)
    if not grouped_findings:
        print("", file=report_stream)
        print(section("Result", kind="ok", stream=report_stream), file=report_stream)
        print(item(ok_message), file=report_stream)
        return

    print("", file=report_stream)
    print(section("Findings", kind="fail", stream=report_stream), file=report_stream)
    current_path: Path | None = None
    for path, line, detail in grouped_findings:
        if path != current_path:
            current_path = path
            print("", file=report_stream)
            print(
                section(str(path), kind="fail", stream=report_stream),
                file=report_stream,
            )
        print(item(render_detail(line, detail), prefix="  "), file=report_stream)

    print("", file=report_stream)
    print(section("Result", kind="warn", stream=report_stream), file=report_stream)
    print(
        item(
            empty_message
            or f"{len(grouped_findings)} finding(s) across {len({entry[0] for entry in grouped_findings})} file(s)."
        ),
        file=report_stream,
    )


def emit_json_report(
    title: str,
    payload: Mapping[str, object],
    *,
    summary: Sequence[tuple[str, object]] = (),
    findings: Sequence[str] = (),
    ok_message: str | None = None,
    report_stream: TextIO | None = None,
    json_stream: TextIO | None = None,
) -> None:
    report_stream = report_stream or sys.stdout
    json_stream = json_stream or report_stream
    print(heading(title, stream=report_stream), file=report_stream)
    if summary:
        print("", file=report_stream)
        print(section("Summary", kind="info", stream=report_stream), file=report_stream)
        for label, value in summary:
            print(
                item(key_value(label, value, stream=report_stream), prefix="  "),
                file=report_stream,
            )
    print("", file=report_stream)
    if findings:
        print(
            section("Findings", kind="fail", stream=report_stream), file=report_stream
        )
        for finding in findings:
            print(item(finding, prefix="  "), file=report_stream)
    else:
        print(section("Result", kind="ok", stream=report_stream), file=report_stream)
        print(item(ok_message or "no findings."), file=report_stream)
    print(json.dumps(dict(payload), sort_keys=True), file=json_stream)


def emit_plain_findings(
    header: str,
    findings: Sequence[str],
    *,
    footer: str | None = None,
    report_stream: TextIO | None = None,
    trailing_blank_line: bool = False,
) -> None:
    """Emit a simple line-based findings block for legacy validators."""
    report_stream = report_stream or sys.stderr
    print(header, file=report_stream)
    if findings:
        print("\n".join(findings), file=report_stream)
    if footer:
        print(footer, file=report_stream)
    if trailing_blank_line:
        print("", file=report_stream)
