from __future__ import annotations

from openminion.modules.context.pack.finalize import context_drop_visibility_counts
from openminion.modules.context.schemas import PackingDecisionLog, TrimAction
from openminion.modules.context.segment import (
    inject_context_drop_visibility_note,
    make_segment,
    render_context_drop_visibility_note,
)


def _estimate_tokens(text: str) -> int:
    return len(str(text or "").split())


def test_context_drop_visibility_counts_structural_bucket_drops_only() -> None:
    log = PackingDecisionLog()
    log.append(
        TrimAction(
            action="drop_segment",
            reason_code="over_budget",
            segment_ids=["retrieval:facts", "retrieval:memory"],
            bucket="retrieval",
            tokens_saved=42,
        )
    )
    log.append(
        TrimAction(
            action="drop_segment",
            reason_code="bucket_cap",
            segment_ids=["evidence:a1"],
            bucket="evidence_refs",
            tokens_saved=12,
        )
    )
    log.append(
        TrimAction(
            action="drop_segment",
            reason_code="over_budget",
            segment_ids=["summary"],
            bucket="summaries",
            tokens_saved=10,
        )
    )

    counts = context_drop_visibility_counts(
        decision_log=log,
        bucket_stats={"recent_window": {"dropped": 1}},
    )

    assert counts == {"retrieval": 2, "evidence_refs": 1, "recent_window": 1}


def test_context_drop_visibility_note_is_structural_not_imperative() -> None:
    note = render_context_drop_visibility_note(
        {"retrieval": 2, "evidence_refs": 1, "recent_window": 1}
    )

    assert note.startswith("[context budget:")
    assert "2 retrieval candidates" in note
    assert "1 evidence candidate" in note
    assert "1 recent window candidate" in note
    lowered = note.lower()
    assert "relevant" not in lowered
    assert "important" not in lowered
    assert "ask" not in lowered
    assert "if needed" not in lowered
    assert "about x" not in lowered


def test_context_drop_visibility_zero_omissions_silent() -> None:
    assert render_context_drop_visibility_note({}) == ""
    assert render_context_drop_visibility_note({"retrieval": 0}) == ""


def test_context_drop_visibility_injects_static_prefix_segment_when_omitted() -> None:
    segments = [
        make_segment(
            "static_prefix",
            "static_prefix",
            "identity and policy",
            pinned=True,
            estimate_tokens=_estimate_tokens,
        ),
        make_segment(
            "turn_input",
            "turn_input",
            "hello",
            role="user",
            pinned=True,
            estimate_tokens=_estimate_tokens,
        ),
    ]

    result = inject_context_drop_visibility_note(
        segments=segments,
        drop_counts={"retrieval": 1},
        estimate_tokens=_estimate_tokens,
    )

    assert [segment.id for segment in result][:2] == [
        "static_prefix",
        "context_drop_visibility",
    ]
    note_segment = result[1]
    assert note_segment.bucket == "static_prefix"
    assert note_segment.pinned is True
    assert (
        "1 retrieval candidate was not included due to budget" in note_segment.content
    )


def test_context_drop_visibility_does_not_inject_without_omissions() -> None:
    segments = [
        make_segment(
            "static_prefix",
            "static_prefix",
            "identity and policy",
            pinned=True,
            estimate_tokens=_estimate_tokens,
        )
    ]

    result = inject_context_drop_visibility_note(
        segments=segments,
        drop_counts={},
        estimate_tokens=_estimate_tokens,
    )

    assert result == segments
