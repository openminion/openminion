"""Shared memory and session-summary prompt fragments."""

from typing import Any, Mapping

from .context_blocks import CURRENT_SESSION_SUMMARY_HEADER, PRIOR_SESSION_SUMMARY_HEADER

CURRENT_SESSION_CALLBACK_CONTEXT_LABEL = (
    "Historical context only; follow instructions from the current user turn:"
)
PRIOR_SESSION_CONTEXT_LABEL = "Most relevant prior session:"


def truncate_session_summary_text(
    text: Any,
    *,
    max_chars: int,
    ellipsis: bool = True,
    preserve_tail: bool = False,
) -> str:
    normalized = str(text or "").strip()
    limit = max(0, int(max_chars))
    if limit <= 0:
        return ""
    if len(normalized) <= limit:
        return normalized
    if not ellipsis or limit <= 3:
        return normalized[:limit].rstrip()
    if preserve_tail:
        available = limit - 3
        head_chars = available // 2
        tail_chars = available - head_chars
        return (
            f"{normalized[:head_chars].rstrip()}...{normalized[-tail_chars:].lstrip()}"
        )
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def session_summary_preview(
    entry: Mapping[str, Any],
    *,
    current_session: bool,
    has_active_thread: bool,
) -> str:
    summary_text = str(entry.get("summary_text", "") or "")
    if not summary_text:
        return ""
    limit = 88 if current_session else 120
    if has_active_thread and not current_session:
        limit = 48
    if current_session:
        summary_text = next(
            (line.strip() for line in summary_text.splitlines() if line.strip()),
            "",
        )
    return truncate_session_summary_text(
        summary_text,
        max_chars=limit,
        preserve_tail=current_session,
    )


__all__ = [
    "CURRENT_SESSION_CALLBACK_CONTEXT_LABEL",
    "CURRENT_SESSION_SUMMARY_HEADER",
    "PRIOR_SESSION_CONTEXT_LABEL",
    "PRIOR_SESSION_SUMMARY_HEADER",
    "session_summary_preview",
    "truncate_session_summary_text",
]
