"""Shared finalization prompt guidance fragments."""

FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE = (
    "After substantive tool-backed work, return the user-facing final answer and "
    'append <finalization_status>{"status":"final_answer|incomplete|blocked",'
    '"reasoning":"...","remaining_work":"...","blocking_reason":"..."}'
    "</finalization_status>. Use final_answer only when the requested deliverable "
    "is actually complete. Use incomplete when more work remains. Use blocked "
    "when you cannot finish truthfully. Keep the answer text before the "
    "finalization_status trailer."
)

FINALIZATION_STATUS_RETRY_GUIDANCE = (
    "Your prior answer omitted the required typed "
    "<finalization_status>...</finalization_status> trailer for substantive "
    "tool-backed work. Reply again with the same user-facing answer and append "
    "the finalization_status trailer."
)

__all__ = [
    "FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE",
    "FINALIZATION_STATUS_RETRY_GUIDANCE",
]
