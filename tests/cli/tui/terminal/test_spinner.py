from __future__ import annotations

import ast
from pathlib import Path

import pytest

from openminion.cli.tui.terminal.spinner import (
    SPINNER_FRAMES,
    THINKING_VERB,
    VERBS,
    Spinner,
    format_status_row,
)


@pytest.mark.parametrize(
    ("start_time", "rotate_seconds", "now", "expected"),
    [
        (0.0, 3.0, 0.0, 0),
        (0.0, 3.0, 2.9, 0),
        (0.0, 3.0, 3.0, 1),
        (0.0, 3.0, 5.9, 1),
        (0.0, 3.0, 6.0, 2),
        (100.0, 3.0, 100.0, 0),
        (100.0, 3.0, 103.0, 1),
        (100.0, 3.0, 99.0, 0),
    ],
)
def test_current_verb_rotation_contract(
    start_time: float, rotate_seconds: float, now: float, expected: int
) -> None:
    spinner = Spinner(start_time=start_time, rotate_seconds=rotate_seconds)
    assert spinner.current_verb(now) == VERBS[expected]


def test_current_verb_wraps_around_after_full_cycle() -> None:
    spinner = Spinner(start_time=0.0, rotate_seconds=3.0)
    cycle_seconds = 3.0 * len(VERBS)
    assert spinner.current_verb(cycle_seconds) == VERBS[0]
    assert spinner.current_verb(cycle_seconds + 3.0) == VERBS[1]


def test_current_verb_returns_empty_in_plain_mode() -> None:
    spinner = Spinner(start_time=0.0, plain=True)
    assert spinner.current_verb(0.0) == ""
    assert spinner.current_verb(15.0) == ""
    assert spinner.current_frame(15.0) == ""


def test_current_frame_uses_old_chat_spinner_frames() -> None:
    spinner = Spinner(start_time=0.0)
    assert spinner.current_frame(0.0) == SPINNER_FRAMES[0]
    assert spinner.current_frame(0.1) == SPINNER_FRAMES[1]
    assert spinner.current_frame(1.0) == SPINNER_FRAMES[0]


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0.0, "0.0s"),
        (0.5, "0.5s"),
        (9.9, "9.9s"),
        (10.0, "10s"),
        (12.7, "12s"),
        (59.9, "59s"),
        (60.0, "1m00s"),
        (60.5, "1m00s"),
        (61.0, "1m01s"),
        (65.0, "1m05s"),
        (83.0, "1m23s"),
        (185.0, "3m05s"),
    ],
)
def test_elapsed_label_formats_by_time_band(elapsed: float, expected: str) -> None:
    spinner = Spinner(start_time=0.0)
    assert spinner.elapsed_label(elapsed) == expected


def test_format_status_row_default_includes_verb_elapsed_hint() -> None:
    text = format_status_row("Cogitating", "12s")
    rendered = text.plain
    assert "✻" in rendered
    assert "Cogitating" in rendered
    assert "12s" in rendered
    assert "esc to interrupt" in rendered


def test_format_status_row_plain_mode_drops_verb() -> None:
    text = format_status_row("Cogitating", "12s", plain=True)
    rendered = text.plain
    assert "Cogitating" not in rendered
    assert "✻" not in rendered
    assert "12s" in rendered
    assert "esc to interrupt" in rendered


def test_format_status_row_custom_hint() -> None:
    text = format_status_row("Pondering", "5s", hint="Ctrl+C to cancel")
    assert "Ctrl+C to cancel" in text.plain


def test_format_status_row_prefers_runtime_status_label() -> None:
    text = format_status_row(
        "Pondering",
        "5s",
        status_label="Analyzing request...",
        spinner_frame="⠋",
    )
    rendered = text.plain
    assert "⠋" in rendered
    assert "Analyzing request..." in rendered
    assert "5s" in rendered
    assert "Pondering" not in rendered


def test_format_status_row_no_hint() -> None:
    text = format_status_row("Pondering", "5s", hint="")
    assert "esc to interrupt" not in text.plain


def test_verbs_tuple_has_at_least_20_entries() -> None:
    assert len(VERBS) >= 20


def test_verbs_tuple_has_no_duplicates() -> None:
    assert len(set(VERBS)) == len(VERBS)


def test_thinking_verb_is_a_string() -> None:
    assert isinstance(THINKING_VERB, str)
    assert THINKING_VERB.strip()


def test_spinner_module_imports_only_neutral_dependencies() -> None:
    src_path = (
        Path(__file__).resolve().parents[4]
        / "src"
        / "openminion"
        / "cli"
        / "tui"
        / "terminal"
        / "spinner.py"
    )
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden_prefixes = (
        "openminion.cli.tui.terminal.streaming",
        "openminion.cli.tui.terminal.shell",
        "openminion.cli.tui.terminal.transcript",
        "openminion.cli.tui.terminal.composer",
        "openminion.cli.tui.terminal.status_line",
        "openminion.cli.tui.terminal.overlays",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for prefix in forbidden_prefixes:
                assert not module.startswith(prefix), (
                    f"spinner.py imports {module} — would create cycle"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"spinner.py imports {alias.name} — would create cycle"
                    )


@pytest.mark.parametrize(
    "elapsed,expected_verb_index",
    [
        (0.0, 0),
        (3.0, 1),
        (6.0, 2),
        (90.0, 0),  # full cycle of 30 verbs at 3s
    ],
)
def test_verb_index_math_matches_spec(elapsed, expected_verb_index) -> None:
    spinner = Spinner(start_time=0.0, rotate_seconds=3.0)
    assert spinner.current_verb(elapsed) == VERBS[expected_verb_index % len(VERBS)]
