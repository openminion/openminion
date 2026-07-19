from __future__ import annotations

from collections.abc import Sequence

_MAX_PREVIEW_WIDTH = 72


def queue_text_preview(text: str, *, max_width: int = _MAX_PREVIEW_WIDTH) -> str:
    preview = " ".join(str(text or "").split())
    if len(preview) <= max_width:
        return preview
    return f"{preview[: max(0, max_width - 1)].rstrip()}…"


def queued_message_notice(count: int) -> str:
    noun = "message" if int(count) == 1 else "messages"
    return f"Queued {noun} ({int(count)} pending)."


def queue_empty_notice() -> str:
    return "No queued messages."


def queue_listing(entries: Sequence[str]) -> str:
    if not entries:
        return queue_empty_notice()
    lines = ["Queued messages:"]
    for index, text in enumerate(entries, start=1):
        lines.append(f"  {index}. {queue_text_preview(text)}")
    lines.append("")
    lines.append("Use `/queue drop <index>`, `/queue clear`, or `/queue run-next`.")
    return "\n".join(lines)


def queue_cleared_notice(count: int) -> str:
    if int(count) <= 0:
        return queue_empty_notice()
    noun = "message" if int(count) == 1 else "messages"
    return f"Cleared {int(count)} queued {noun}."


def queue_drop_notice(index: int, text: str) -> str:
    return f"Dropped queued message {int(index)}: {queue_text_preview(text)}"


def queue_drop_usage_notice() -> str:
    return "Usage: /queue drop <index>"


def queue_drop_missing_notice(index: int) -> str:
    return f"No queued message at index {int(index)}."


def queue_run_next_empty_notice() -> str:
    return "No queued message is available to run next."


def queue_run_next_notice(text: str) -> str:
    return f"Running queued message: {queue_text_preview(text)}"


def queue_preserved_after_interrupt_notice(count: int) -> str:
    noun = "message" if int(count) == 1 else "messages"
    return (
        f"Interrupted current turn. Preserved {int(count)} queued {noun}; "
        "use `/queue run-next` or `/queue clear`."
    )


def queue_command_usage_notice() -> str:
    return "Usage: /queue [clear|drop <index>|run-next]"


def is_queue_command(text: str) -> bool:
    stripped = str(text or "").strip()
    return stripped == "/queue" or stripped.startswith("/queue ")


__all__ = [
    "is_queue_command",
    "queue_cleared_notice",
    "queue_command_usage_notice",
    "queue_drop_missing_notice",
    "queue_drop_notice",
    "queue_drop_usage_notice",
    "queue_empty_notice",
    "queue_listing",
    "queue_preserved_after_interrupt_notice",
    "queue_run_next_empty_notice",
    "queue_run_next_notice",
    "queue_text_preview",
    "queued_message_notice",
]
