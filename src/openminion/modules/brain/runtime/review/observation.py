from typing import Any

from ...constants import (
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_CONTINUE,
)
from ...schemas.closure import (
    ClosureJudgment,
    ReviewFact,
)
from ..budget.continuation import has_continuation_budget


REVIEW_TOOL_NAME = "review.diff"
REVIEW_BLOCK_REASON = "review_block"
_VALID_SEVERITIES: tuple[str, ...] = ("ok", "warn", "block", "unavailable")


def observe_review_invocation(
    tool_results: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> ReviewFact:
    """Observe review invocations and build a typed fact."""
    if not tool_results:
        return ReviewFact()
    review_results = [
        item
        for item in tool_results
        if isinstance(item, dict)
        and str(item.get("tool_name") or "").strip().lower() == REVIEW_TOOL_NAME
    ]
    if not review_results:
        return ReviewFact()
    last_ok = None
    for item in review_results:
        if bool(item.get("ok")):
            last_ok = item
    chosen = last_ok if last_ok is not None else review_results[-1]
    data = chosen.get("data")
    findings_count = 0
    severity = "unavailable"
    if isinstance(data, dict):
        raw_count = data.get("findings_count")
        if isinstance(raw_count, bool):
            findings_count = int(raw_count)
        elif isinstance(raw_count, int):
            findings_count = max(0, raw_count)
        elif isinstance(raw_count, str) and raw_count.strip().isdigit():
            findings_count = int(raw_count.strip())
        raw_severity = str(data.get("severity") or "").strip().lower()
        if raw_severity in _VALID_SEVERITIES:
            severity = raw_severity
        elif last_ok is not None:
            severity = "ok"
    elif last_ok is not None:
        severity = "ok"
    return ReviewFact(
        invoked=True,
        findings_count=findings_count,
        severity=severity,  # type: ignore[arg-type]
    )


def is_review_blocking(fact: ReviewFact | None) -> bool:
    """Return whether the review reported a blocking severity."""
    return fact is not None and fact.invoked and fact.severity == "block"


def apply_review_to_judgment(
    judgment: ClosureJudgment,
    fact: ReviewFact,
    *,
    state: Any,
) -> ClosureJudgment:
    """Apply review-derived overrides to a closure judgment."""
    judgment.review = fact
    if not is_review_blocking(fact):
        return judgment
    if not (judgment.satisfied and judgment.next_action == BRAIN_DISPOSITION_CLOSE):
        return judgment
    if has_continuation_budget(state):
        judgment.satisfied = False
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
    judgment.reason = (
        f"{judgment.reason}; {REVIEW_BLOCK_REASON}"
        if judgment.reason
        else REVIEW_BLOCK_REASON
    )
    return judgment


__all__ = [
    "REVIEW_BLOCK_REASON",
    "REVIEW_TOOL_NAME",
    "apply_review_to_judgment",
    "is_review_blocking",
    "observe_review_invocation",
]
