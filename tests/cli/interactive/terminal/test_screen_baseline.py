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
from prompt_toolkit.data_structures import Size
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
from openminion.tools.commerce.confirmation import (
    ExactOrderConfirmationPreview,
    commerce_confirmation_lines,
    commerce_result_lines,
)
from openminion.tools.commerce.models import (
    CommerceLifecycleState,
    Money,
)
from openminion.tools.commerce.provider import (
    OrderActionPreparation,
    OrderInspection,
    RefundDestination,
)


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
_COMMERCE_DIGEST = "sha256:" + "a" * 64


async def _wait_for_output(raw: io.StringIO, text: str) -> None:
    for _ in range(200):
        if text in raw.getvalue():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"terminal did not render {text!r}")


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
    raise AssertionError(f"terminal row {row} did not render {text!r}: {rows}")


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
        await asyncio.sleep(0.01)
        snapshot = raw.getvalue()
        pipe.send_text("baseline-exit\r")
        await read_task
    return snapshot


async def _render_inline_layout_checkpoints(
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
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        await asyncio.sleep(0.01)
        write_task = writer(lambda: console.print("Previous response\nDone in 13s"))
        assert write_task is not None
        await write_task
        await asyncio.sleep(0.01)
        placeholder = _screen_contract(raw.getvalue(), width=width, height=rows)

        pipe.send_text("test")
        await _wait_for_output(raw, "test")
        typed = _screen_contract(raw.getvalue(), width=width, height=rows)

        pipe.send_text("\r")
        await read_task
    return placeholder, typed


async def _render_flow_layout_checkpoints() -> tuple[dict, dict]:
    raw = io.StringIO()
    output = Vt100_Output(
        raw,
        get_size=lambda: Size(rows=40, columns=100),
        default_color_depth=ColorDepth.TRUE_COLOR,
        enable_cpr=False,
    )
    status_line = TerminalStatusLine()
    status_line.set_state(
        agent="minimax-m2-7", model="MiniMax-M2.7", cwd="/repo", state="idle"
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
        console = Console(file=raw, force_terminal=True, color_system=None, width=100)
        writer = build_prompt_safe_terminal_writer(
            console=console, prompt_session=composer.prompt_session
        )
        console.print("Greeting\nTip")
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        await asyncio.sleep(0.01)
        initial = _screen_contract(raw.getvalue(), width=100, height=40)

        pipe.send_text("hi\r")
        assert await read_task == "hi"
        writer(lambda: console.print("❯ hi\n● Hello"))
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "● Hello")
        await asyncio.sleep(0.01)
        answered = _screen_contract(raw.getvalue(), width=100, height=40)

        pipe.send_text("exit\r")
        await read_task
    return initial, answered


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
        pipe.send_text("first turn\r")
        await read_task

        read_task = asyncio.create_task(composer.read_line())
        await asyncio.sleep(0.02)
        repeated = raw.getvalue()
        pipe.send_text("baseline-exit\r")
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
        output.write("Greeting\r\nTip\r\n")
        output.flush()
        composer = TerminalComposer(
            slash_commands=slash_commands,
            bottom_toolbar=status_line.bottom_toolbar,
            active_status=status_line.active_status,
            color=False,
        )
        composer.set_busy(busy)
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")
        pipe.send_text("/")
        await _wait_for_screen_row(
            raw,
            width=width,
            height=rows,
            row=4 if busy else 2,
            text="❯ /",
        )
        await asyncio.sleep(0.01)
        snapshot = _screen_contract(raw.getvalue(), width=width, height=rows)

        pipe.send_text("\r")
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


def _capture_commerce_scene(*, width: int) -> dict:
    lifecycle = CommerceLifecycleState(
        order="accepted",
        fulfillment="partial",
        payment="captured",
        shipments={"outbound-1": "delivered", "return-1": "return_in_transit"},
    )
    order = ExactOrderConfirmationPreview(
        merchant="[red]Fixture\nMerchant\x1b[2J",
        seller="Fixture Seller",
        preparation_ref="prep-1",
        items=(
            {
                "offer_id": "offer-1",
                "variant_id": "blue",
                "quantity": 1,
                "line_total_minor": 2800,
                "returnable": True,
                "final_sale": False,
            },
        ),
        discount_minor=0,
        tax_minor=200,
        shipping_minor=300,
        fees_minor=0,
        total_minor=2800,
        currency="USD",
        destination_label="Home ending 42",
        buyer_profile_digest=_COMMERCE_DIGEST,
        destination_digest=_COMMERCE_DIGEST,
        payment_label="Visa ending 4242",
        payment_destination_digest=_COMMERCE_DIGEST,
        recurring=False,
        checkout_revision="checkout-r1",
        expires_at="2026-10-06T20:00:00+00:00",
        preparation_digest=_COMMERCE_DIGEST,
        subject_id="local",
        session_id="session-1",
    )
    blocks = [
        commerce_confirmation_lines(order),
        commerce_result_lines(
            OrderInspection(
                reference="order-1",
                revision="order-r1",
                items=(),
                lifecycle=lifecycle,
                total=Money(currency="USD", amount_minor=2800),
            )
        ),
        commerce_result_lines(
            OrderActionPreparation(
                action_ref="action-1",
                action_revision="action-1:r1",
                order_ref="order-1",
                order_revision="order-r1",
                kind="refund_request",
                eligible=True,
                consequence="Refund the selected item.",
                fees=Money(currency="USD", amount_minor=0),
                refund=Money(currency="USD", amount_minor=2500),
                refund_method="original_payment_method",
                refund_destination=RefundDestination(
                    destination_digest=_COMMERCE_DIGEST,
                    label="Visa ending 4242",
                ),
                expires_at="2026-10-06T20:00:00+00:00",
                action_digest=_COMMERCE_DIGEST,
            )
        ),
    ]
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        no_color=True,
        width=width,
        height=180,
    )
    for lines in blocks:
        console.print("\n".join(lines))
        console.print()
    return _screen_contract(buffer.getvalue(), width=width, height=180)


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
        read_task = asyncio.create_task(composer.read_line())
        await _wait_for_output(raw, "❯")

        terminal_rows = 42
        composer.invalidate()
        await _wait_for_screen_row(
            raw,
            width=100,
            height=42,
            row=2,
            text="❯ Type to queue for the next turn · Esc interrupts",
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
                row=2 + len(redraws),
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

        pipe.send_text("!")
        await _wait_for_screen_row(
            raw,
            width=100,
            height=42,
            row=3,
            text="❯ typing while busy!",
        )
        reanchored = _screen_contract(
            raw.getvalue(),
            width=100,
            height=42,
            include_cursor_visibility=True,
        )

        pipe.send_text("\r")
        await read_task
    return {
        "typing": typing,
        "redraws": redraws,
        "completed": completed,
        "extended": reanchored,
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


@pytest.mark.parametrize("width", [48, 72, 100], ids=["narrow", "standard", "wide"])
def test_commerce_presentations_stay_readable_across_resizes(width: int) -> None:
    scene = _capture_commerce_scene(width=width)
    visible = " ".join(row["text"].strip() for row in scene["rows"])

    assert all(len(row["text"]) <= width for row in scene["rows"])
    assert "Exact order review" in visible and "Order tracking" in visible
    assert "Order care review" in visible and "Fixture Merchant" in visible
    assert "Refund method: original payment method" in visible
    assert "Refund destination: Visa ending 4242" in visible
    assert visible.count("Next action:") >= 2
    assert "\x1b" not in visible


def test_composer_scenes_start_at_current_transcript_position(
    screen_contract: dict,
) -> None:
    for name, scene in screen_contract.items():
        if not name.startswith("composer_"):
            continue
        footer_rows = [
            row["row"] for row in scene["rows"] if row["text"].startswith("◆ ")
        ]
        assert footer_rows
        assert footer_rows[0] < 23
        assert scene["cursor"]["y"] < footer_rows[0]


def test_short_conversation_keeps_composer_next_to_transcript() -> None:
    initial, answered = asyncio.run(_render_flow_layout_checkpoints())
    initial_rows = {row["row"]: row["text"] for row in initial["rows"]}
    answered_rows = {row["row"]: row["text"] for row in answered["rows"]}

    assert initial_rows[0] == "Greeting"
    assert initial_rows[1] == "Tip"
    assert initial_rows[2].startswith("❯ Ask anything")
    assert initial_rows[3].startswith("◆ minimax-m2-7")
    assert answered_rows[2] == "❯ hi"
    assert answered_rows[3] == "● Hello"
    assert answered_rows[4].startswith("❯ Ask anything")
    assert answered_rows[5].startswith("◆ minimax-m2-7")


def test_composer_follows_inherited_shell_output_without_a_gap() -> None:
    checkpoints = asyncio.run(_render_inherited_terminal_checkpoints())

    for raw in checkpoints:
        scene = _strict_screen_contract(raw, width=153, height=47)
        rendered_rows = {row["row"]: row["text"] for row in scene["rows"]}

        assert "\x1b[999;" not in raw
        assert rendered_rows[12] == "shell setup"
        assert rendered_rows[13].startswith("❯ Ask anything")
        assert rendered_rows[14].startswith("◆ minimax-m2-7")
        assert scene["cursor"]["y"] == 13


@pytest.mark.parametrize("enable_cpr", [False, True], ids=["no-cpr", "cpr-capable"])
@pytest.mark.parametrize(
    ("rows", "width"),
    [(18, 72), (24, 100), (42, 140)],
    ids=["compact", "standard", "wide"],
)
def test_composer_keeps_input_after_output_without_bottom_gap(
    enable_cpr: bool,
    rows: int,
    width: int,
) -> None:
    placeholder, typed = asyncio.run(
        _render_inline_layout_checkpoints(
            enable_cpr=enable_cpr,
            rows=rows,
            width=width,
        )
    )

    for scene in (placeholder, typed):
        rendered_rows = {row["row"]: row["text"] for row in scene["rows"]}
        assert "Previous response" in rendered_rows.values()
        assert "Done in 13s" in rendered_rows.values()
        assert rendered_rows[3].startswith("◆ minimax-m2-7")
        assert scene["cursor"]["y"] == 2
        assert sum(text.startswith("◆ ") for text in rendered_rows.values()) == 1
        assert sum("❯" in text for text in rendered_rows.values()) == 1
        assert not any(
            "cursor position requests" in text for text in rendered_rows.values()
        )

    placeholder_rows = {row["row"]: row["text"] for row in placeholder["rows"]}
    typed_rows = {row["row"]: row["text"] for row in typed["rows"]}
    assert placeholder_rows[2].startswith("❯ Ask anything")
    assert typed_rows[2] == "❯ test"


def test_inline_layout_tracks_busy_typing_output_and_resize() -> None:
    checkpoints = asyncio.run(_render_terminal_resize_checkpoints())
    typing = checkpoints["typing"]
    redraws = checkpoints["redraws"]
    completed = checkpoints["completed"]
    extended = checkpoints["extended"]
    assert isinstance(typing, list)
    assert isinstance(redraws, list)
    assert isinstance(completed, dict)
    assert isinstance(extended, dict)

    expected_draft = ""
    for index, (character, scene) in enumerate(
        zip("typing while busy", typing, strict=True), start=1
    ):
        expected_draft += character
        rows = {row["row"]: row["text"] for row in scene["rows"]}
        update_count = sum(index > at for at in (4, 10, 17))
        prompt_row = update_count + 2
        assert rows[prompt_row] == f"❯ {expected_draft}".rstrip()
        assert rows[prompt_row + 1].startswith("◆ minimax-m2-7")
        assert rows[update_count].startswith("Status: Reviewing request...")
        assert scene["cursor"] == {
            "x": len(expected_draft) + 2,
            "y": prompt_row,
            "hidden": False,
        }

    for update, checkpoint in enumerate(redraws, start=1):
        draft = checkpoint["draft"]
        scene = checkpoint["scene"]
        rows = {row["row"]: row["text"] for row in scene["rows"]}
        assert rows[update + 2] == f"❯ {draft}".rstrip()
        assert rows[update + 3].startswith("◆ minimax-m2-7")
        assert rows[update].startswith("Status: Reviewing request...")
        assert scene["cursor"] == {
            "x": len(draft) + 2,
            "y": update + 2,
            "hidden": False,
        }
        assert any(row["text"] == f"Response update {update}" for row in scene["rows"])

    completed_rows = {row["row"]: row["text"] for row in completed["rows"]}
    assert completed_rows[3] == "❯ typing while busy"
    assert completed_rows[4].startswith("◆ minimax-m2-7")
    assert not any(text.startswith("Status:") for text in completed_rows.values())
    assert completed["cursor"] == {"x": 19, "y": 3, "hidden": False}
    assert any(row["text"] == "Response update 3" for row in completed["rows"])

    extended_rows = {row["row"]: row["text"] for row in extended["rows"]}
    assert extended_rows[3] == "❯ typing while busy!"
    assert extended_rows[4].startswith("◆ minimax-m2-7")
    assert extended["cursor"] == {"x": 20, "y": 3, "hidden": False}


@pytest.mark.parametrize("busy", [False, True], ids=["idle", "busy"])
@pytest.mark.parametrize(
    ("height", "width"),
    [(18, 72), (24, 100), (42, 140)],
    ids=["compact", "standard", "wide"],
)
def test_completion_menu_opens_below_inline_input(
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
        if row > 0 and text.strip().startswith("/")
    ]

    assert rendered_rows[0] == "Greeting"
    assert rendered_rows[1] == "Tip"
    prompt_row = 4 if busy else 2
    assert scene["cursor"]["y"] == prompt_row
    assert rendered_rows[prompt_row] == "❯ /"
    footer_rows = [row for row, text in rendered_rows.items() if text.startswith("◆ ")]
    assert len(footer_rows) == 1
    assert footer_rows[0] < height - 1
    assert "/agents" in menu_rows
    assert all(" " not in text for text in menu_rows)
    assert not any("Status:" in text and "/" in text for text in rendered_rows.values())
    if busy:
        assert any(
            text.startswith("Status: Analyzing request...")
            for text in rendered_rows.values()
        )
