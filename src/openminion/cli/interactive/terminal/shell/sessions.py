from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.styles import StyleToken

from ..overlays import TerminalOverlayPresenter
from ..transcript import TerminalTranscript

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"


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
    non_empty = [
        item for item in sessions if int(getattr(item, "message_count", 0) or 0) > 0
    ]
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
