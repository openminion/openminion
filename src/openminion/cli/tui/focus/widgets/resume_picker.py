from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

from openminion.cli.tui.widgets.session_picker import (
    SessionPickerModal,
    _NewSessionRow,
    _PickerRow,
)


class _ResumePickerRow(_PickerRow):
    """Resume-specific row: id/name + relative age + count + preview."""

    def __init__(self, session: dict, index: int) -> None:
        sid = str(session.get("id", "")).strip()
        name = str(session.get("name", "")).strip()
        age = str(session.get("age", "")).strip()
        count = int(session.get("message_count", 0) or 0)
        preview = str(session.get("preview_line", "")).strip()
        display = name if name else sid[:20]
        meta_bits = []
        if age:
            meta_bits.append(age)
        meta_bits.append(f"{count} msg" if count == 1 else f"{count} msgs")
        meta = "  " + "  ".join(meta_bits)
        preview_display = ""
        if preview:
            truncated = preview if len(preview) <= 70 else preview[:69] + "…"
            preview_display = f"\n      {truncated}"
        Static.__init__(
            self,
            f"  {display:<24}{meta}{preview_display}",
            classes="picker-row",
        )
        self._session_id = sid
        self._index = index


class ResumePickerScreen(SessionPickerModal):
    """Resume picker — non-empty sessions with preview + age + count."""

    TITLE_TEXT = "Resume a session  (↑↓ Enter · Esc to cancel)"

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(self.TITLE_TEXT, id="picker-title")
            if self._sessions:
                for i, session in enumerate(self._sessions):
                    yield _ResumePickerRow(session, i)
            else:
                yield Label(
                    "No prior sessions with messages found.",
                    classes="dim-hint",
                )
            yield _NewSessionRow()


def relative_age(updated_at: str, *, now: datetime | None = None) -> str:
    """Format an ISO timestamp as a relative-age string like ``2h ago``."""
    if not updated_at:
        return ""
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    delta = reference - ts
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def build_resume_dicts(
    sessions: Iterable[Any],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Normalize an iterable of session records into picker dicts."""
    out: list[dict] = []
    for record in sessions:
        sid = str(getattr(record, "id", "") or "").strip()
        if not sid:
            continue
        name = str(getattr(record, "name", "") or "").strip()
        if not name:
            metadata = getattr(record, "metadata", None) or {}
            if isinstance(metadata, dict):
                name = str(metadata.get("name", "") or "").strip()
        updated_at = (
            str(getattr(record, "updated_at", "") or "").strip()
            or str(getattr(record, "last_activity_at", "") or "").strip()
        )
        age = relative_age(updated_at, now=now)
        msg_count = getattr(record, "message_count", None)
        if msg_count is None:
            msg_count = getattr(record, "turn_count", 0)
        try:
            msg_count_int = int(msg_count or 0)
        except (TypeError, ValueError):
            msg_count_int = 0
        preview = str(getattr(record, "preview_line", "") or "").strip()
        out.append(
            {
                "id": sid,
                "name": name,
                "age": age,
                "message_count": msg_count_int,
                "preview_line": preview,
            }
        )
    return out


__all__ = ["ResumePickerScreen", "build_resume_dicts", "relative_age"]
