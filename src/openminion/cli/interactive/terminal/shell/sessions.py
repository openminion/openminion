from __future__ import annotations

import asyncio
from collections.abc import Callable
from threading import Event
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.interactive.runtime.messages import room_result_chat_messages
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.styles import StyleToken
from openminion.modules.telemetry.trace.phase_timing import mark_active_chat_first_text

from ..overlays import TerminalOverlayPresenter
from ..transcript import TerminalTranscript

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"


async def run_room_turn_if_bound(
    runtime: Any,
    text: str,
    *,
    progress_callback: Callable[[dict[str, Any]], None],
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None,
    transcript: TerminalTranscript,
    handle: Any,
) -> str | None:
    room_runner = getattr(runtime, "run_room_turn", None)
    room_detector = getattr(runtime, "is_room_session", None)
    if not callable(room_runner) or not callable(room_detector) or not room_detector():
        return None
    cancel_event = Event()
    try:
        messages = room_result_chat_messages(
            await room_runner(
                text,
                progress_callback=progress_callback,
                approval_callback=approval_callback,
                cancel_event=cancel_event,
            )
        )
    except asyncio.CancelledError:
        cancel_event.set()
        raise
    if not messages:
        return ""
    first, *remaining = messages
    if transcript._messages:
        transcript._messages[-1].sender = first.sender
        transcript._messages[-1].msg_id = first.msg_id
    if first.sender:
        handle.append_renderable(Text(first.sender, style="bold"))
    for message in remaining:
        transcript.push_message(message)
    mark_active_chat_first_text()
    return str(first.body)


def runtime_message_stream(
    runtime: Any,
    text: str,
    progress_callback: Callable[[dict[str, Any]], None],
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None,
) -> Any:
    kwargs: dict[str, Any] = {"progress_callback": progress_callback}
    if approval_callback is not None:
        kwargs["approval_callback"] = approval_callback
    return runtime.send_message(text, **kwargs)


def handle_room_slash(
    cmd: str,
    args: str,
    *,
    runtime: Any,
    console: Console,
) -> None:
    parts = str(args or "").split()
    try:
        if cmd == "/participants":
            body = runtime.room_participants_report()
        elif cmd == "/invite":
            if len(parts) == 2 and parts[0] == "agent":
                participant = runtime.room_invite_agent(parts[1])
            elif len(parts) in {2, 3} and parts[0] == "human":
                participant = runtime.room_invite_human(
                    parts[1],
                    role=parts[2] if len(parts) == 3 else "participant",
                )
            else:
                raise ValueError(
                    "usage: /invite agent <id> or /invite human <id> [role]"
                )
            body = (
                f"invited {participant.participant_type} "
                f"{participant.participant_id} as {participant.role}"
            )
        elif cmd == "/kick":
            if len(parts) != 2:
                raise ValueError("usage: /kick <agent|human> <id>")
            removed = runtime.room_kick(parts[0], parts[1])
            body = "participant removed" if removed else "participant not found"
        elif cmd == "/activate":
            if len(parts) != 1:
                raise ValueError("usage: /activate <agent-id>")
            runtime.room_activate(parts[0])
            body = f"active room agent: {parts[0]}"
        elif cmd == "/routing":
            if not parts:
                body = runtime.room_participants_report()
            elif len(parts) == 1:
                runtime.room_set_routing(parts[0])
                body = f"room routing: {parts[0].lower()}"
            else:
                raise ValueError("usage: /routing [addressed|broadcast|sequential]")
        else:
            return
    except (RuntimeError, ValueError) as exc:
        body = f"{cmd}: {exc}"
    console.print(Text(body, style=token_rich_style(StyleToken.SYSTEM)))


def start_new_session(
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
) -> None:
    creator = getattr(runtime, "create_new_session", None)
    if not callable(creator):
        console.print(
            Text("(runtime does not expose create_new_session)", style=_MUTED_STYLE)
        )
        return
    try:
        session_id = str(creator() or "").strip()
    except Exception as exc:
        console.print(Text(f"(could not start new session: {exc})", style=_ERR_STYLE))
        return
    transcript.clear_messages()
    message = (
        f"(started new session: {session_id})"
        if session_id
        else "(started new session)"
    )
    console.print(Text(message, style=_MUTED_ITALIC_STYLE))


def resume_session(
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
) -> None:
    lister = getattr(runtime, "list_directory_sessions", None)
    binder = getattr(runtime, "bind_session", None)
    history_getter = getattr(runtime, "get_current_history", None)
    if not callable(lister) or not callable(binder) or not callable(history_getter):
        console.print(
            Text("(runtime does not expose resume session helpers)", style=_MUTED_STYLE)
        )
        return
    try:
        sessions = list(lister(limit=50) or [])
    except Exception as exc:
        console.print(Text(f"(could not list sessions: {exc})", style=_ERR_STYLE))
        return
    non_empty = [item for item in sessions if _session_message_count(item) > 0]
    if not non_empty:
        console.print(
            Text(
                "No prior sessions with messages found in this directory. "
                "Use `/new` to start one.",
                style=_MUTED_ITALIC_STYLE,
            )
        )
        return
    chosen_id = str(overlay.present_resume_picker(non_empty) or "").strip()
    if not chosen_id:
        return
    try:
        binder(chosen_id)
        history = list(history_getter() or [])
    except Exception as exc:
        console.print(Text(f"(could not resume session: {exc})", style=_ERR_STYLE))
        return
    transcript.set_messages(history)
    console.print(Text(f"(resumed session: {chosen_id})", style=_MUTED_ITALIC_STYLE))


def _session_message_count(item: Any) -> int:
    if isinstance(item, dict):
        value = item.get("message_count")
        meta = item.get("meta")
        if value is None and isinstance(meta, dict):
            value = meta.get("message_count")
    else:
        value = getattr(item, "message_count", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
