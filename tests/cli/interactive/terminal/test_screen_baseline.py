"""Deterministic screen-grid baselines for Focus terminal rendering."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import re
from unittest.mock import patch

import pyte
import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Point, Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

from openminion.cli.interactive.terminal.composer import TerminalComposer
from openminion.cli.interactive.terminal.prompt_output import (
    build_prompt_safe_terminal_writer,
)
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


async def _wait_for_renderer_height(composer: TerminalComposer) -> None:
    for _ in range(200):
        if composer.prompt_session.app.renderer.height_is_known:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("terminal did not report its cursor position")


async def _wait_for_screen_row(
    raw: io.StringIO,
    *,
    width: int,
    height: int,
    row: int,
    text: str,
) -> None:
    for _ in range(200):
        scene = _screen_contract(raw.getvalue(), width=width, height=height)
        rows = {item["row"]: item["text"] for item in scene["rows"]}
        if rows.get(row) == text:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"terminal row {row} did not render {text!r}")


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
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=output),
    ):
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

        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        await _wait_for_renderer_height(composer)
        await asyncio.sleep(0.01)
        snapshot = raw.getvalue()
        pipe.send_text("baseline-exit\n")
        await read_task
    return snapshot


async def _render_bottom_layout_checkpoints(
    *,
    enable_cpr: bool,
    rows: int,
    width: int,
) -> tuple[dict, dict]:
    raw = io.StringIO()
    output = Vt100_Output(
        raw,
        get_size=lambda: Size(rows=rows, columns=width),
        default_color_depth=ColorDepth.TRUE_COLOR,
        enable_cpr=enable_cpr,
    )
    status_line = TerminalStatusLine()
    status_line.set_state(
        agent="minimax-m2-7",
        model="MiniMax-M2.7",
        cwd="/repo/openminion",
        state="idle",
    )

    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=output),
    ):
        composer = TerminalComposer(
            bottom_toolbar=status_line.bottom_toolbar,
            color=False,
        )
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        await _wait_for_renderer_height(composer)
        await asyncio.sleep(0.01)

        console = Console(
            file=raw,
            force_terminal=True,
            color_system=None,
            width=width,
        )
        writer = build_prompt_safe_terminal_writer(
            console=console,
            prompt_session=composer.prompt_session,
        )
        write_task = writer(lambda: console.print("Previous response\nDone in 13s"))
        assert write_task is not None
        await write_task
        await _wait_for_renderer_height(composer)
        await asyncio.sleep(0.01)
        placeholder = _screen_contract(raw.getvalue(), width=width, height=rows)

        pipe.send_text("test")
        await _wait_for_output(raw, "test")
        typed = _screen_contract(raw.getvalue(), width=width, height=rows)

        pipe.send_text("\n")
        await read_task
    return placeholder, typed


async def _render_inherited_terminal_checkpoints() -> tuple[str, str]:
    """Render after shell output in a strict 47x153 macOS-style terminal."""

    raw = io.StringIO()
    raw.write("shell setup\r\n" * 13)
    output = Vt100_Output(
        raw,
        get_size=lambda: Size(rows=47, columns=153),
        default_color_depth=ColorDepth.TRUE_COLOR,
        enable_cpr=True,
    )
    status_line = TerminalStatusLine()
    status_line.set_state(
        agent="minimax-m2-7",
        model="MiniMax-M2.7",
        cwd="/repo/openminion",
        state="idle",
    )

    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=output),
    ):
        composer = TerminalComposer(
            bottom_toolbar=status_line.bottom_toolbar,
            color=False,
        )
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        await asyncio.sleep(0.01)
        initial = raw.getvalue()
        pipe.send_text("first turn\n")
        await read_task

        read_task = asyncio.create_task(composer.read_line())
        await asyncio.sleep(0.02)
        repeated = raw.getvalue()
        pipe.send_text("baseline-exit\n")
        await read_task
    return initial, repeated


async def _render_completion_layout_checkpoint(
    *,
    busy: bool,
    rows: int,
    width: int,
) -> dict:
    raw = io.StringIO()
    output = Vt100_Output(
        raw,
        get_size=lambda: Size(rows=rows, columns=width),
        default_color_depth=ColorDepth.TRUE_COLOR,
        enable_cpr=True,
    )
    status_line = TerminalStatusLine()
    status_line.set_state(
        agent="minimax-m2-7",
        model="MiniMax-M2.7",
        cwd="/repo/openminion",
        state="responding" if busy else "idle",
        turn_status="Analyzing request..." if busy else "",
        elapsed_seconds=2,
    )
    slash_commands = {
        "/agents": "list agents",
        "/clear": "clear transcript",
        "/context": "show context",
        "/exit": "exit",
        "/help": "show help",
        "/history": "show history",
        "/model": "select model",
        "/permissions": "show permissions",
        "/session": "show session",
        "/theme": "select theme",
    }

    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=output),
    ):
        composer = TerminalComposer(
            slash_commands=slash_commands,
            bottom_toolbar=status_line.bottom_toolbar,
            active_status=status_line.active_status,
            color=False,
        )
        composer.set_busy(busy)
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        await _wait_for_renderer_height(composer)
        pipe.send_text("/")
        await _wait_for_output(raw, "/agents")
        await asyncio.sleep(0.01)
        snapshot = _screen_contract(raw.getvalue(), width=width, height=rows)

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


def _screen_contract(
    raw: str,
    *,
    width: int,
    height: int = 24,
    include_cursor_visibility: bool = False,
) -> dict:
    assert "\x1b[?1049h" not in raw
    normalized = raw.replace("\r\n", "\n").replace("\n", "\r\n")
    screen = pyte.Screen(width, height)
    pyte.Stream(screen).feed(normalized)

    rows = []
    for row, text in enumerate(screen.display):
        visible = text.rstrip()
        styles = _styled_spans(screen, row)
        if not visible and not styles:
            continue
        rows.append({"row": row, "text": visible, "styles": styles})
    cursor = {"x": screen.cursor.x, "y": screen.cursor.y}
    if include_cursor_visibility:
        cursor["hidden"] = screen.cursor.hidden
    return {
        "width": width,
        "cursor": cursor,
        "rows": rows,
    }


def _strict_screen_contract(raw: str, *, width: int, height: int) -> dict:
    """Model terminals that ignore CUP requests beyond the physical screen."""

    def keep_in_bounds(match: re.Match[str]) -> str:
        return match.group(0) if int(match.group(1)) <= height else ""

    strict_raw = re.sub(r"\x1b\[(\d+);(\d+)H", keep_in_bounds, raw)
    return _screen_contract(strict_raw, width=width, height=height)


async def _render_terminal_resize_checkpoints() -> dict[str, object]:
    raw = io.StringIO()
    terminal_rows = 24

    def get_size() -> Size:
        return Size(rows=terminal_rows, columns=100)

    output = Vt100_Output(
        raw,
        get_size=get_size,
        default_color_depth=ColorDepth.TRUE_COLOR,
        enable_cpr=False,
    )
    status_line = TerminalStatusLine()
    status_line.set_state(
        agent="minimax-m2-7",
        model="MiniMax-M2.7",
        cwd="/repo/openminion",
        state="responding",
        turn_status="Reviewing request...",
        elapsed_seconds=4,
    )

    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=output),
    ):
        composer = TerminalComposer(
            bottom_toolbar=status_line.bottom_toolbar,
            active_status=status_line.active_status,
            color=False,
        )
        composer.set_busy(True)
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")

        terminal_rows = 42
        composer.invalidate()
        await _wait_for_screen_row(
            raw,
            width=100,
            height=42,
            row=40,
            text="❯ Type to queue for the next turn · Esc interrupts",
        )

        console = Console(
            file=raw,
            force_terminal=True,
            color_system=None,
            width=100,
        )
        writer = build_prompt_safe_terminal_writer(
            console=console,
            prompt_session=composer.prompt_session,
        )
        typing = []
        redraws = []
        draft = ""
        for index, character in enumerate("typing while busy", start=1):
            draft += character
            pipe.send_text(character)
            await _wait_for_screen_row(
                raw,
                width=100,
                height=42,
                row=40,
                text=f"❯ {draft}".rstrip(),
            )
            await asyncio.sleep(0.005)
            typing.append(
                _screen_contract(
                    raw.getvalue(),
                    width=100,
                    height=42,
                    include_cursor_visibility=True,
                )
            )

            if index in (4, 10, 17):
                update = len(redraws) + 1
                write_task = writer(
                    lambda update=update: console.print(f"Response update {update}")
                )
                assert write_task is not None
                await write_task
                await asyncio.sleep(0.01)
                redraws.append(
                    {
                        "draft": draft,
                        "scene": _screen_contract(
                            raw.getvalue(),
                            width=100,
                            height=42,
                            include_cursor_visibility=True,
                        ),
                    }
                )

        status_line.set_state(state="idle", turn_status="")
        composer.set_busy(False)
        await asyncio.sleep(0.01)
        completed = _screen_contract(
            raw.getvalue(),
            width=100,
            height=42,
            include_cursor_visibility=True,
        )

        renderer = composer.prompt_session.app.renderer
        output.cursor_goto(row=15, column=1)
        output.flush()
        renderer._cursor_pos = Point(x=0, y=0)
        renderer._last_screen = None
        composer.invalidate()
        await asyncio.sleep(0.02)
        reanchored = _screen_contract(
            raw.getvalue(),
            width=100,
            height=42,
            include_cursor_visibility=True,
        )

        pipe.send_text("\n")
        await read_task
    return {
        "typing": typing,
        "redraws": redraws,
        "completed": completed,
        "reanchored": reanchored,
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


def test_composer_scenes_pin_input_and_footer_to_terminal_bottom(
    screen_contract: dict,
) -> None:
    for name, scene in screen_contract.items():
        if not name.startswith("composer_"):
            continue
        footer_rows = [
            row["row"] for row in scene["rows"] if row["text"].startswith("◆ ")
        ]
        assert footer_rows == [23]
        assert scene["cursor"]["y"] == 22


def test_composer_uses_exact_terminal_edge_after_inherited_shell_output() -> None:
    checkpoints = asyncio.run(_render_inherited_terminal_checkpoints())

    for raw in checkpoints:
        scene = _strict_screen_contract(raw, width=153, height=47)
        rendered_rows = {row["row"]: row["text"] for row in scene["rows"]}

        assert "\x1b[999;" not in raw
        assert rendered_rows[45].startswith("❯ Ask anything")
        assert rendered_rows[46].startswith("◆ minimax-m2-7")
        assert scene["cursor"]["y"] == 45


@pytest.mark.parametrize("enable_cpr", [False, True], ids=["no-cpr", "cpr-capable"])
@pytest.mark.parametrize(
    ("rows", "width"),
    [(18, 72), (24, 100), (42, 140)],
    ids=["compact", "standard", "wide"],
)
def test_composer_keeps_input_above_footer_after_prompt_safe_output(
    enable_cpr: bool,
    rows: int,
    width: int,
) -> None:
    placeholder, typed = asyncio.run(
        _render_bottom_layout_checkpoints(
            enable_cpr=enable_cpr,
            rows=rows,
            width=width,
        )
    )

    for scene in (placeholder, typed):
        rendered_rows = {row["row"]: row["text"] for row in scene["rows"]}
        assert "Previous response" in rendered_rows.values()
        assert "Done in 13s" in rendered_rows.values()
        assert rendered_rows[rows - 1].startswith("◆ minimax-m2-7")
        assert scene["cursor"]["y"] == rows - 2
        assert sum(text.startswith("◆ ") for text in rendered_rows.values()) == 1
        assert sum("❯" in text for text in rendered_rows.values()) == 1
        assert not any(
            "cursor position requests" in text for text in rendered_rows.values()
        )

    placeholder_rows = {row["row"]: row["text"] for row in placeholder["rows"]}
    typed_rows = {row["row"]: row["text"] for row in typed["rows"]}
    assert placeholder_rows[rows - 2].startswith("❯ Ask anything")
    assert typed_rows[rows - 2] == "❯ test"


def test_bottom_layout_reanchors_when_reported_terminal_height_changes() -> None:
    checkpoints = asyncio.run(_render_terminal_resize_checkpoints())
    typing = checkpoints["typing"]
    redraws = checkpoints["redraws"]
    completed = checkpoints["completed"]
    reanchored = checkpoints["reanchored"]
    assert isinstance(typing, list)
    assert isinstance(redraws, list)
    assert isinstance(completed, dict)
    assert isinstance(reanchored, dict)

    expected_draft = ""
    for character, scene in zip("typing while busy", typing, strict=True):
        expected_draft += character
        rows = {row["row"]: row["text"] for row in scene["rows"]}
        assert rows[40] == f"❯ {expected_draft}".rstrip()
        assert rows[41].startswith("◆ minimax-m2-7")
        assert rows[38].startswith("Status: Reviewing request...")
        assert scene["cursor"] == {
            "x": len(expected_draft) + 2,
            "y": 40,
            "hidden": False,
        }

    for update, checkpoint in enumerate(redraws, start=1):
        draft = checkpoint["draft"]
        scene = checkpoint["scene"]
        rows = {row["row"]: row["text"] for row in scene["rows"]}
        assert rows[40] == f"❯ {draft}".rstrip()
        assert rows[41].startswith("◆ minimax-m2-7")
        assert rows[38].startswith("Status: Reviewing request...")
        assert scene["cursor"] == {
            "x": len(draft) + 2,
            "y": 40,
            "hidden": False,
        }
        assert any(row["text"] == f"Response update {update}" for row in scene["rows"])

    completed_rows = {row["row"]: row["text"] for row in completed["rows"]}
    assert completed_rows[40] == "❯ typing while busy"
    assert completed_rows[41].startswith("◆ minimax-m2-7")
    assert not any(
        row >= 38 and text.startswith("Status:") for row, text in completed_rows.items()
    )
    assert completed["cursor"] == {"x": 19, "y": 40, "hidden": False}
    assert any(row["text"] == "Response update 3" for row in completed["rows"])

    reanchored_rows = {row["row"]: row["text"] for row in reanchored["rows"]}
    assert reanchored_rows[40] == "❯ typing while busy"
    assert reanchored_rows[41].startswith("◆ minimax-m2-7")
    assert reanchored["cursor"] == {"x": 19, "y": 40, "hidden": False}


@pytest.mark.parametrize("busy", [False, True], ids=["idle", "busy"])
@pytest.mark.parametrize(
    ("height", "width"),
    [(18, 72), (24, 100), (42, 140)],
    ids=["compact", "standard", "wide"],
)
def test_completion_menu_opens_above_anchored_input(
    busy: bool,
    height: int,
    width: int,
) -> None:
    scene = asyncio.run(
        _render_completion_layout_checkpoint(
            busy=busy,
            rows=height,
            width=width,
        )
    )
    rendered_rows = {row["row"]: row["text"] for row in scene["rows"]}
    menu_rows = [
        text.strip()
        for row, text in rendered_rows.items()
        if row < height - 2 and text.strip().startswith("/")
    ]

    assert scene["cursor"]["y"] == height - 2
    assert rendered_rows[height - 2] == "❯ /"
    assert rendered_rows[height - 1].startswith("◆ minimax-m2-7")
    assert "/agents" in menu_rows
    assert all(" " not in text for text in menu_rows)
    assert not any("Status:" in text and "/" in text for text in rendered_rows.values())
    if busy:
        assert any(
            text.startswith("Status: Analyzing request...")
            for text in rendered_rows.values()
        )
