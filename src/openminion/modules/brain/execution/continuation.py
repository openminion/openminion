from ..schemas import WorkingState

_CONTINUATION_CHOICE_TOKENS = frozenset({"continue", "retry", "cancel"})
_RESUME_LIKE_INPUTS = frozenset(
    {
        "resume",
        "continue",
        "continue plan",
        "continue previous plan",
        "continue with previous plan",
    }
)


def has_pending_continuation_reply(state: WorkingState) -> bool:
    return bool(getattr(state, "awaiting_continuation_reply", False))


def parse_continuation_choice(text: str | None) -> str:
    normalized = str(text or "").strip().lower()
    return normalized if normalized in _CONTINUATION_CHOICE_TOKENS else "unclear"


def is_resume_like_input(text: str | None) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    return normalized in _RESUME_LIKE_INPUTS


def continuation_choice_message(reason: str | None) -> str:
    guidance = str(reason or "").strip()
    base = (
        "The previous step completed successfully, but it did not fully satisfy the goal."
        + (f" Closure guidance: {guidance}" if guidance else "")
    )
    return (
        f"{base}\n"
        "Reply 'continue' to choose a distinct action, "
        "'retry' to reassess the original request, or 'cancel' to stop."
    )


def clear_continuation_reply(
    state: WorkingState,
    *,
    clear_guard: bool,
) -> None:
    state.awaiting_continuation_reply = False
    if clear_guard:
        state.continuation_guard_command_signature = None
        state.continuation_guard_reason = ""
