BRAIN_DECIDE_BLOCKER_FAMILY_INTERNAL_FAILURE = "internal_failure"
BRAIN_DECIDE_BLOCKER_FAMILY_REAL_BLOCKER = "real_blocker"

_INTERNAL_FAILURE_REASON_CODES = frozenset(
    {
        "decision_validation_failed",
        "llm_empty_response",
        "tool_envelope_unexecutable",
        "two_step_budget_exhausted",
        "two_step_classification_failed",
        "two_step_payload_failed",
    }
)
_INTERNAL_FAILURE_REASON_PREFIXES = ("invalid_decide_",)


def is_internal_failure_reason_code(reason_code: str | None) -> bool:
    normalized = str(reason_code or "").strip()
    return bool(normalized) and (
        normalized in _INTERNAL_FAILURE_REASON_CODES
        or normalized.startswith(_INTERNAL_FAILURE_REASON_PREFIXES)
    )


def decide_blocker_family(*, reason_code: str | None, mode: str | None = None) -> str:
    if is_internal_failure_reason_code(reason_code):
        return BRAIN_DECIDE_BLOCKER_FAMILY_INTERNAL_FAILURE
    return (
        BRAIN_DECIDE_BLOCKER_FAMILY_REAL_BLOCKER
        if str(mode or "").strip().lower() == "respond"
        else BRAIN_DECIDE_BLOCKER_FAMILY_INTERNAL_FAILURE
    )


__all__ = [
    "BRAIN_DECIDE_BLOCKER_FAMILY_INTERNAL_FAILURE",
    "BRAIN_DECIDE_BLOCKER_FAMILY_REAL_BLOCKER",
    "decide_blocker_family",
    "is_internal_failure_reason_code",
]
