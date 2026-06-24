from __future__ import annotations

from openminion.modules.brain.execution.intent_state import (
    build_partial_success_summary,
)
from openminion.modules.brain.schemas import IntentExecutionState


def _intent(
    intent_id: str,
    description: str,
    *,
    status: str,
    summary: str = "",
) -> IntentExecutionState:
    return IntentExecutionState(
        intent_id=intent_id,
        description=description,
        status=status,
        summary=summary,
    )


def test_build_partial_success_summary_returns_none_when_all_intents_succeed() -> None:
    summary = build_partial_success_summary(
        [
            _intent("find_a", "Find A", status="succeeded", summary="Found A."),
            _intent("find_b", "Find B", status="succeeded", summary="Found B."),
        ]
    )

    assert summary is None


def test_build_partial_success_summary_formats_completed_and_remaining_items() -> None:
    summary = build_partial_success_summary(
        [
            _intent("find_a", "Find A", status="succeeded", summary="Found A."),
            _intent("find_b", "Find B", status="pending"),
            _intent("find_c", "Find C", status="failed", summary="tool error"),
        ]
    )

    assert summary is not None
    assert "Completed:" in summary
    assert "- [done] Find A - Found A." in summary
    assert "Not completed:" in summary
    assert "- [pending] Find B - not reached before this turn ended" in summary
    assert "- [pending] Find C - tool error" in summary
    assert "Reply 'continue' to resume the remaining work." in summary


def test_build_partial_success_summary_formats_all_failed_without_completed_block() -> (
    None
):
    summary = build_partial_success_summary(
        [
            _intent("find_a", "Find A", status="failed", summary="network failed"),
            _intent("find_b", "Find B", status="blocked"),
        ]
    )

    assert summary is not None
    assert "Completed:" not in summary
    assert "Not completed:" in summary
    assert "- [pending] Find A - network failed" in summary
    assert "- [pending] Find B - blocked before completion" in summary


def test_build_partial_success_summary_returns_none_for_empty_state() -> None:
    assert build_partial_success_summary([]) is None
    assert build_partial_success_summary(None) is None
