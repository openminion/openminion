from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.runtime.failures import (
    FailurePatternBucket,
    FailurePatternReadout,
    TypedFailureFact,
    aggregate_failure_patterns,
    project_seam_emissions_to_facts,
)


def _emission(
    reason_code: str = "SEARCH_FAULT_NETWORK_TIMEOUT",
    *,
    trace_id: str = "trace-1",
    session_id: str = "sess-1",
    recorded_at: str = "2026-05-13T10:00:00Z",
    context_kind: str = "tool_call",
    via_details: bool = False,
) -> SimpleNamespace:
    fields = {
        "reason_code": reason_code,
        "trace_id": trace_id,
        "session_id": session_id,
        "recorded_at": recorded_at,
        "context_kind": context_kind,
    }
    if via_details:
        return SimpleNamespace(details=fields)
    return SimpleNamespace(**fields)


# --- Projection: closed-set seam_id discipline ----------------------------


def test_projection_rejects_unknown_seam_id() -> None:
    with pytest.raises(KeyError):
        project_seam_emissions_to_facts([_emission()], seam_id="not_a_seam")  # type: ignore[arg-type]


def test_projection_emits_one_fact_per_emission() -> None:
    emissions = [
        _emission(reason_code="SEARCH_FAULT_NETWORK_TIMEOUT"),
        _emission(reason_code="SEARCH_FAULT_RATE_LIMITED"),
    ]
    facts = project_seam_emissions_to_facts(emissions, seam_id="search_provider")
    assert [f.reason_code for f in facts] == [
        "SEARCH_FAULT_NETWORK_TIMEOUT",
        "SEARCH_FAULT_RATE_LIMITED",
    ]
    assert all(f.seam_id == "search_provider" for f in facts)


def test_projection_accepts_attribute_or_details_shape() -> None:
    emissions = [
        _emission(via_details=False),
        _emission(reason_code="POLICY_DENIED_REPO", via_details=True),
    ]
    facts = project_seam_emissions_to_facts(emissions, seam_id="github_policy")
    assert {f.reason_code for f in facts} == {
        "SEARCH_FAULT_NETWORK_TIMEOUT",
        "POLICY_DENIED_REPO",
    }


def test_projection_blank_reason_code_falls_to_seam_specific_unknown() -> None:
    emissions = [_emission(reason_code="")]
    facts = project_seam_emissions_to_facts(emissions, seam_id="adaptive_termination")
    assert facts[0].reason_code == "adaptive_termination_unknown"


def test_projection_strategy_outcome_failure_maps_to_typed_reason() -> None:
    emissions = [
        SimpleNamespace(content={"outcome_status": "failure", "session_id": "s-1"})
    ]
    facts = project_seam_emissions_to_facts(emissions, seam_id="strategy_outcome")
    assert facts[0].reason_code == "strategy_outcome_failure"


def test_projection_skips_emission_with_no_extractable_mapping() -> None:
    emissions = [SimpleNamespace()]  # no details/content/attrs
    facts = project_seam_emissions_to_facts(emissions, seam_id="search_provider")
    assert facts == []


# --- Aggregation discipline -----------------------------------------------


def test_aggregate_groups_by_seam_id_and_reason_code() -> None:
    facts = [
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="SEARCH_FAULT_NETWORK_TIMEOUT",
            session_id="sess-1",
        ),
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="SEARCH_FAULT_NETWORK_TIMEOUT",
            session_id="sess-2",
        ),
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="SEARCH_FAULT_RATE_LIMITED",
            session_id="sess-1",
        ),
    ]
    readout = aggregate_failure_patterns(facts)
    assert readout.total_facts_scanned == 3
    assert readout.distinct_seam_reason_pairs == 2
    top = readout.rows[0]
    assert top.seam_id == "search_provider"
    assert top.reason_code == "SEARCH_FAULT_NETWORK_TIMEOUT"
    assert top.recurrence_count == 2
    assert top.distinct_sessions == 2


def test_aggregate_sort_descending_count_then_alphabetical() -> None:
    facts = [
        TypedFailureFact(seam_id="search_provider", reason_code="ZZ"),
        TypedFailureFact(seam_id="search_provider", reason_code="ZZ"),
        TypedFailureFact(seam_id="github_policy", reason_code="POLICY_DENIED_REPO"),
        TypedFailureFact(seam_id="adaptive_termination", reason_code="AA"),
    ]
    readout = aggregate_failure_patterns(facts)
    # ZZ has 2 → first; then alphabetical between AA and POLICY_DENIED_REPO
    # (both count=1; sort by (seam_id, reason_code))
    assert readout.rows[0].reason_code == "ZZ"
    # Remaining order: adaptive_termination < github_policy (alphabetical on seam_id)
    remaining = [(r.seam_id, r.reason_code) for r in readout.rows[1:]]
    assert remaining == [
        ("adaptive_termination", "AA"),
        ("github_policy", "POLICY_DENIED_REPO"),
    ]


def test_aggregate_distinct_traces_and_sessions_dedup_blanks() -> None:
    facts = [
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="X",
            trace_id="t-1",
            session_id="s-1",
        ),
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="X",
            trace_id="t-1",
            session_id="s-1",
        ),
        TypedFailureFact(
            seam_id="search_provider", reason_code="X", trace_id="", session_id=""
        ),
    ]
    readout = aggregate_failure_patterns(facts)
    assert readout.rows[0].distinct_traces == 1
    assert readout.rows[0].distinct_sessions == 1


def test_aggregate_empty_returns_empty_readout() -> None:
    readout = aggregate_failure_patterns([])
    assert readout.rows == []
    assert readout.total_facts_scanned == 0
    assert readout.distinct_seam_reason_pairs == 0


def test_aggregate_evidence_window_pass_through() -> None:
    window = {"agent_id": "agent-1", "time_range": "last-24h"}
    readout = aggregate_failure_patterns([], evidence_window=window)
    assert readout.evidence_window == window


def test_aggregate_earliest_latest_timestamps_sorted() -> None:
    facts = [
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="X",
            recorded_at="2026-05-13T12:00:00Z",
        ),
        TypedFailureFact(
            seam_id="search_provider",
            reason_code="X",
            recorded_at="2026-05-13T10:00:00Z",
        ),
    ]
    readout = aggregate_failure_patterns(facts)
    assert readout.rows[0].earliest_recorded_at == "2026-05-13T10:00:00Z"
    assert readout.rows[0].latest_recorded_at == "2026-05-13T12:00:00Z"


# --- Anti-LLM regression --------------------------------------------------


def test_schemas_do_not_expose_root_cause_or_pattern_label_fields() -> None:
    forbidden_substrings = (
        "confusion",
        "hesitation",
        "root_cause",
        "pattern_label",
        "severity",
        "narrative",
        "summary",
        "label",
    )
    schema_fields = (
        set(TypedFailureFact.model_fields.keys())
        | set(FailurePatternBucket.model_fields.keys())
        | set(FailurePatternReadout.model_fields.keys())
    )
    for field_name in schema_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in field_name, (
                f"FPAC discipline violation: field {field_name!r} contains "
                f"forbidden substring {forbidden!r}."
            )


def test_all_seam_ids_are_acceptable_to_projection() -> None:
    for seam_id in (
        "search_provider",
        "controlplane_route",
        "gateway_memory",
        "github_policy",
        "approval_decision",
        "adaptive_termination",
        "strategy_outcome",
        "low_progress",
    ):
        # Should not raise on empty emissions list.
        facts = project_seam_emissions_to_facts([], seam_id=seam_id)  # type: ignore[arg-type]
        assert facts == []


def test_aggregate_is_deterministic() -> None:
    facts = [
        TypedFailureFact(seam_id="search_provider", reason_code="X"),
        TypedFailureFact(seam_id="search_provider", reason_code="Y"),
    ]
    a = aggregate_failure_patterns(facts)
    b = aggregate_failure_patterns(facts)
    assert a.model_dump() == b.model_dump()
