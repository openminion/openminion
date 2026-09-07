"""Memory-owned retrieval eligibility shared by recall and surfacing."""

from __future__ import annotations

from typing import Any

from sophiagraph.models import MemoryScope
from sophiagraph.query.retrieval_types import RetrievalEligibilityDecision


def retrieval_eligibility(
    record: Any, *, minimum_confidence: float
) -> RetrievalEligibilityDecision:
    scope = str(getattr(record, "scope", "") or "")
    record_type = str(getattr(record, "type", "") or "")
    try:
        parsed_scope = MemoryScope.parse(scope)
    except ValueError:
        parsed_scope = None
    if (parsed_scope is not None and parsed_scope.is_session) or record_type in {
        "session_summary",
        "pin",
    }:
        return RetrievalEligibilityDecision(eligible=True, reason_code="eligible")
    confidence = float(getattr(record, "confidence", 0.0) or 0.0)
    if confidence < float(minimum_confidence):
        return RetrievalEligibilityDecision(
            eligible=False,
            reason_code="below_retrieval_confidence",
        )
    return RetrievalEligibilityDecision(eligible=True, reason_code="eligible")
