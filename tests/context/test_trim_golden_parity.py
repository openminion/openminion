from __future__ import annotations

from openminion.modules.context.constants import PINNED_BUCKETS, TRIM_ORDER
from openminion.modules.context.schemas import ContextSegment, PackingDecisionLog
from openminion.modules.context.segment import apply_trim_ladder, make_segment


def _est(text: str) -> int:
    return len(text.split())


def _seg(
    seg_id: str, bucket: str, content: str, *, pinned: bool = False
) -> ContextSegment:
    return make_segment(
        seg_id,
        bucket,
        content,
        role="system",
        pinned=pinned,
        estimate_tokens=_est,
    )


def _surviving_ids(segments: list[ContextSegment]) -> list[str]:
    return [s.id for s in segments if s.content.strip()]


def _drop_actions(log: PackingDecisionLog) -> list[tuple[str, str, str]]:
    return [
        (a.bucket or "", a.reason_code, a.segment_ids[0] if a.segment_ids else "")
        for a in log.actions
        if a.action == "drop_segment"
    ]


def test_drop_order_evidence_first_then_retrieval() -> None:
    segments = [
        _seg("static_prefix", "static_prefix", "alpha beta gamma", pinned=True),
        _seg("turn_input", "turn_input", "user query", pinned=True),
        _seg("retrieval:facts", "retrieval", "fact one fact two fact three"),
        _seg("evidence:art1", "evidence_refs", "art one art two art three"),
    ]
    bucket_caps = {
        b: 100 for b in {"retrieval", "evidence_refs", "summaries", "recent_window"}
    }
    log = PackingDecisionLog()
    warnings: list[str] = []

    result_segments, result_log, _ = apply_trim_ladder(
        segments,
        total_cap=11,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=warnings,
        estimate_tokens=_est,
    )

    surviving = _surviving_ids(result_segments)
    drops = _drop_actions(result_log)

    assert drops[0][0] == "evidence_refs", f"expected evidence_refs first, got {drops}"
    assert drops[0][1] == "over_budget"
    assert drops[0][2] == "evidence:art1"
    assert "retrieval:facts" in surviving
    assert "static_prefix" in surviving
    assert "turn_input" in surviving
    assert "evidence:art1" not in surviving


def test_drop_order_summaries_last_in_trim_chain() -> None:
    # Order in TRIM_ORDER: evidence_refs, retrieval, recent_window, summaries
    assert TRIM_ORDER == ["evidence_refs", "retrieval", "recent_window", "summaries"]

    segments = [
        _seg("static_prefix", "static_prefix", "x", pinned=True),
        _seg("turn_input", "turn_input", "q", pinned=True),
        _seg("summary", "summaries", "one two three four five"),
        _seg("turn:t1", "recent_window", "six seven eight"),
        _seg("retrieval:facts", "retrieval", "nine ten"),
        _seg("evidence:e1", "evidence_refs", "eleven"),
    ]
    bucket_caps = {
        b: 100 for b in {"retrieval", "evidence_refs", "summaries", "recent_window"}
    }
    log = PackingDecisionLog()

    result_segments, result_log, _ = apply_trim_ladder(
        segments,
        total_cap=7,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=[],
        estimate_tokens=_est,
    )

    drop_buckets_in_order = [a[0] for a in _drop_actions(result_log)]
    assert drop_buckets_in_order[:3] == ["evidence_refs", "retrieval", "recent_window"]
    surviving = _surviving_ids(result_segments)
    assert "summary" in surviving


def test_bucket_cap_drops_with_bucket_cap_reason() -> None:
    segments = [
        _seg("static_prefix", "static_prefix", "x", pinned=True),
        _seg("turn_input", "turn_input", "q", pinned=True),
        _seg("evidence:e1", "evidence_refs", "alpha beta gamma"),
        _seg("evidence:e2", "evidence_refs", "delta epsilon zeta"),
    ]
    bucket_caps = {
        "evidence_refs": 3,
        "retrieval": 100,
        "summaries": 100,
        "recent_window": 100,
    }
    log = PackingDecisionLog()

    _, result_log, _ = apply_trim_ladder(
        segments,
        total_cap=10_000,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=[],
        estimate_tokens=_est,
    )

    drops = _drop_actions(result_log)
    assert any(d[0] == "evidence_refs" and d[1] == "bucket_cap" for d in drops), (
        f"expected at least one bucket_cap drop in evidence_refs, got {drops}"
    )


def test_bucket_cap_does_not_drop_pinned_bucket() -> None:
    segments = [
        _seg("static_prefix", "static_prefix", "alpha beta gamma delta epsilon"),
        _seg("turn_input", "turn_input", "q", pinned=True),
    ]
    # Set absurdly low caps for pinned buckets - should be ignored
    bucket_caps = {b: 1 for b in PINNED_BUCKETS}
    bucket_caps.update(
        {"evidence_refs": 100, "retrieval": 100, "summaries": 100, "recent_window": 100}
    )
    log = PackingDecisionLog()

    result_segments, result_log, _ = apply_trim_ladder(
        segments,
        total_cap=10_000,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=[],
        estimate_tokens=_est,
    )

    surviving = _surviving_ids(result_segments)
    assert "static_prefix" in surviving
    assert "turn_input" in surviving
    assert _drop_actions(result_log) == []


def test_reason_codes_and_warnings_emitted() -> None:
    segments = [
        _seg("static_prefix", "static_prefix", "x", pinned=True),
        _seg("turn_input", "turn_input", "q", pinned=True),
        _seg("retrieval:facts", "retrieval", "alpha beta gamma delta"),
        _seg("evidence:e1", "evidence_refs", "one two three"),
    ]
    bucket_caps = {
        "retrieval": 100,
        "evidence_refs": 100,
        "summaries": 100,
        "recent_window": 100,
    }
    log = PackingDecisionLog()
    warnings: list[str] = []

    _, result_log, result_warnings = apply_trim_ladder(
        segments,
        total_cap=4,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=warnings,
        estimate_tokens=_est,
    )

    drops = _drop_actions(result_log)
    assert all(d[1] == "over_budget" for d in drops)
    drop_warnings = [w for w in result_warnings if w.startswith("drop_segment:")]
    assert len(drop_warnings) == len(drops)
    for action in result_log.actions:
        if action.action == "drop_segment":
            assert action.tokens_saved > 0


def test_budget_exceeded_warning_when_pinned_blocks_trim() -> None:
    segments = [
        _seg("static_prefix", "static_prefix", "alpha beta gamma delta epsilon zeta"),
        _seg("turn_input", "turn_input", "user query"),
    ]
    bucket_caps = {
        "retrieval": 100,
        "evidence_refs": 100,
        "summaries": 100,
        "recent_window": 100,
    }
    log = PackingDecisionLog()
    warnings: list[str] = []

    _, _, result_warnings = apply_trim_ladder(
        segments,
        total_cap=2,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=warnings,
        estimate_tokens=_est,
    )

    assert any(w.startswith("budget_exceeded:remaining=") for w in result_warnings), (
        f"expected budget_exceeded warning, got {result_warnings}"
    )


def test_pinned_buckets_never_dropped_under_severe_pressure() -> None:
    segments = [
        _seg("static_prefix", "static_prefix", "alpha", pinned=True),
        _seg("mission_snapshot", "mission_snapshot", "beta gamma", pinned=True),
        _seg("active_plan", "active_plan", "delta epsilon", pinned=True),
        _seg("trailer_feedback", "trailer_feedback", "zeta", pinned=True),
        _seg("conversation_summary", "conversation_summary", "eta theta", pinned=True),
        _seg("turn_input", "turn_input", "iota", pinned=True),
        _seg("retrieval:facts", "retrieval", "kappa lambda mu nu xi"),
        _seg("evidence:e1", "evidence_refs", "omicron pi rho sigma"),
        _seg("turn:t1", "recent_window", "tau upsilon"),
        _seg("summary", "summaries", "phi chi"),
    ]
    bucket_caps = {
        "retrieval": 100,
        "evidence_refs": 100,
        "summaries": 100,
        "recent_window": 100,
    }
    log = PackingDecisionLog()

    result_segments, result_log, _ = apply_trim_ladder(
        segments,
        total_cap=1,
        bucket_caps=bucket_caps,
        decision_log=log,
        warnings=[],
        estimate_tokens=_est,
    )

    surviving = _surviving_ids(result_segments)
    # Every pinned-bucket segment survives
    for pinned_id in [
        "static_prefix",
        "mission_snapshot",
        "active_plan",
        "trailer_feedback",
        "conversation_summary",
        "turn_input",
    ]:
        assert pinned_id in surviving, f"{pinned_id} dropped"

    # All non-pinned were dropped
    drops = _drop_actions(result_log)
    dropped_ids = [d[2] for d in drops]
    for non_pinned in ["retrieval:facts", "evidence:e1", "turn:t1", "summary"]:
        assert non_pinned in dropped_ids, f"{non_pinned} should have been dropped"

    # No drop action targets a pinned bucket
    drop_buckets = {d[0] for d in drops}
    assert drop_buckets.isdisjoint(PINNED_BUCKETS)
