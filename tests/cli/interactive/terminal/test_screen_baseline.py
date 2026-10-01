"""Deterministic screen-grid baselines for Focus terminal rendering."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

import pyte
import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

from openminion.cli.interactive.terminal.composer import TerminalComposer
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.animation.models import (
    AnimationResolution,
    AnimationSpec,
)
from openminion.cli.presentation.models import ToolEvent
from openminion.cli.presentation.styles import set_active_theme, set_color_mode
from openminion.cli.theme import DARK, LIGHT


_BASELINE_PATH = Path(__file__).with_name("baselines") / "screen_contract.json"
_DEFAULT_STYLE = (
    "default",
    "default",
    False,
    False,
    False,
    False,
    False,
    False,
)


async def _wait_for_output(raw: io.StringIO, text: str) -> None:
    for _ in range(200):
        if text in raw.getvalue():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"terminal did not render {text!r}")


def _render_completed_turn(console: Console) -> None:
    transcript = TerminalTranscript(console)
    transcript.set_terminal_writer(lambda render: render())
    transcript.render_user_input(
        "Review the renderer while preserving unrelated terminal behavior."
    )
    handle = transcript.begin_turn()
    handle.append_tool_block(
        ToolEvent(
            tool_name="read",
            args={"path": "src/openminion/cli/interactive/terminal/streaming.py"},
            content="inspected streaming output\nconfirmed terminal ownership",
            duration_ms=1200,
            exit_code=0,
        )
    )
    handle.append_token(
        "Renderer baseline ready with stable spacing, wrapping, and muted details."
    )
    handle._started_at = 10.0
    with patch(
        "openminion.cli.interactive.terminal.streaming.time.monotonic",
        return_value=13.4,
    ):
        handle.complete()


async def _render_composer(
    *,
    width: int,
    color_enabled: bool,
    busy: bool,
    draft: str = "",
) -> str:
    raw = io.StringIO()
    output = Vt100_Output(
        raw,
        get_size=lambda: Size(rows=24, columns=width),
        default_color_depth=ColorDepth.TRUE_COLOR,
        enable_cpr=True,
    )
    status_line = TerminalStatusLine()
    status_line.set_state(
        agent="minimax-m2-7",
        model="MiniMax-M2.7",
        cwd="/repo/openminion",
        queued_count=2 if busy else 0,
        state="responding" if busy else "idle",
        turn_status=(
            "Reviewing terminal rendering across narrow layouts and queued input"
            if busy
            else ""
        ),
        elapsed_seconds=3,
    )
    composer = TerminalComposer(
        bottom_toolbar=status_line.bottom_toolbar,
        active_status=status_line.active_status,
        animation=AnimationResolution(
            AnimationSpec("unicode", "baseline", ("◐",), 1_000),
            source="flag",
        ),
        color=color_enabled,
    )
    if draft:
        composer.prefill_draft(draft)
    composer.set_busy(busy)

    with create_pipe_input() as pipe:
        composer._session = PromptSession(
            input=pipe,
            output=output,
            key_bindings=composer.prompt_session.key_bindings,
            style=composer.prompt_session.style,
        )
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        pipe.send_bytes(b"\x1b[1;1R")
        await _wait_for_output(raw, "◆ minimax-m2-7")
        snapshot = raw.getvalue()
        pipe.send_text("\n")
        await read_task
    return snapshot


def _style_key(char: object) -> tuple[object, ...]:
    return (
        char.fg,
        char.bg,
        char.bold,
        char.italics,
        char.underscore,
        char.strikethrough,
        char.reverse,
        char.blink,
    )


def _style_fields(style: tuple[object, ...]) -> dict[str, object]:
    fields = dict(
        zip(
            (
                "fg",
                "bg",
                "bold",
                "italics",
                "underscore",
                "strikethrough",
                "reverse",
                "blink",
            ),
            style,
        )
    )
    return {
        key: value for key, value in fields.items() if value not in ("default", False)
    }


def _styled_spans(screen: pyte.Screen, row: int) -> list[dict]:
    spans: list[dict] = []
    start = 0
    current = _style_key(screen.buffer[row][0])
    for column in range(1, screen.columns):
        style = _style_key(screen.buffer[row][column])
        if style == current:
            continue
        if current != _DEFAULT_STYLE:
            spans.append({"columns": [start, column], **_style_fields(current)})
        start = column
        current = style
    if current != _DEFAULT_STYLE:
        spans.append({"columns": [start, screen.columns], **_style_fields(current)})
    return spans


def _screen_contract(raw: str, *, width: int) -> dict:
    assert "\x1b[?1049h" not in raw
    normalized = raw.replace("\r\n", "\n").replace("\n", "\r\n")
    screen = pyte.Screen(width, 24)
    pyte.Stream(screen).feed(normalized)

    rows = []
    for row, text in enumerate(screen.display):
        visible = text.rstrip()
        styles = _styled_spans(screen, row)
        if not visible and not styles:
            continue
        rows.append({"row": row, "text": visible, "styles": styles})
    return {
        "width": width,
        "cursor": {"x": screen.cursor.x, "y": screen.cursor.y},
        "rows": rows,
    }


def _capture_completed_scene(*, width: int, theme, color_mode: str) -> dict:
    buffer = io.StringIO()
    color_enabled = color_mode != "never"
    set_active_theme(theme)
    set_color_mode(color_mode)
    try:
        console = Console(
            file=buffer,
            force_terminal=True,
            force_interactive=False,
            color_system="truecolor",
            no_color=not color_enabled,
            width=width,
            height=24,
        )
        _render_completed_turn(console)
    finally:
        set_active_theme(DARK)
        set_color_mode(None)
    return _screen_contract(buffer.getvalue(), width=width)


def _capture_composer_scene(
    *,
    width: int,
    theme,
    color_mode: str,
    busy: bool,
    draft: str = "",
) -> dict:
    color_enabled = color_mode != "never"
    set_active_theme(theme)
    set_color_mode(color_mode)
    try:
        raw = asyncio.run(
            _render_composer(
                width=width,
                color_enabled=color_enabled,
                busy=busy,
                draft=draft,
            )
        )
    finally:
        set_active_theme(DARK)
        set_color_mode(None)
    return _screen_contract(raw, width=width)


def _current_contract() -> dict:
    return {
        "composer_busy_dark_narrow": _capture_composer_scene(
            width=72,
            theme=DARK,
            color_mode="always",
            busy=True,
            draft=(
                "Keep the queued prompt editable while this deliberately long draft "
                "wraps across the narrow terminal."
            ),
        ),
        "composer_idle_light_standard": _capture_composer_scene(
            width=100,
            theme=LIGHT,
            color_mode="always",
            busy=False,
        ),
        "composer_busy_plain_wide": _capture_composer_scene(
            width=140,
            theme=DARK,
            color_mode="never",
            busy=True,
        ),
        "completed_dark_standard": _capture_completed_scene(
            width=100, theme=DARK, color_mode="always"
        ),
        "completed_light_narrow": _capture_completed_scene(
            width=72, theme=LIGHT, color_mode="always"
        ),
        "completed_plain_wide": _capture_completed_scene(
            width=140, theme=DARK, color_mode="never"
        ),
    }


@pytest.fixture(scope="module")
def screen_contract() -> dict:
    return _current_contract()


def test_screen_contract_matches_reviewed_baseline(screen_contract: dict) -> None:
    expected = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    assert screen_contract == expected


def test_screen_contract_uses_only_reviewed_surface_backgrounds(
    screen_contract: dict,
) -> None:
    allowed_backgrounds = {
        "composer_busy_dark_narrow": {
            DARK.surface_app_bg.removeprefix("#").lower(),
            DARK.surface_panel_bg.removeprefix("#").lower(),
        },
        "composer_idle_light_standard": {
            LIGHT.surface_app_bg.removeprefix("#").lower(),
            LIGHT.surface_panel_bg.removeprefix("#").lower(),
        },
        "composer_busy_plain_wide": set(),
        "completed_dark_standard": set(),
        "completed_light_narrow": set(),
        "completed_plain_wide": set(),
    }
    for name, scene in screen_contract.items():
        for row in scene["rows"]:
            for style in row["styles"]:
                assert "reverse" not in style
                if "bg" in style:
                    assert style["bg"] in allowed_backgrounds[name]
