from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.research import (
    Citation,
    Claim,
    ClaimVerificationResult,
    ResearchComposition,
    ResearchPlan,
    ResearchStep,
    build_citation,
    build_research_composition,
    build_research_step,
    verify_claim,
)


# --- Builders --------------------------------------------------------------


def test_build_research_step_normalizes_text() -> None:
    step = build_research_step(
        step_id="  step-1  ",
        query="  what is X?  ",
        scope="  domain-y  ",
        rationale=" because Z ",
    )
    assert step.step_id == "step-1"
    assert step.query == "what is X?"
    assert step.scope == "domain-y"
    assert step.rationale == "because Z"


def test_build_research_step_requires_nonempty_step_id_and_query() -> None:
    with pytest.raises(Exception):
        build_research_step(step_id="", query="x")
    with pytest.raises(Exception):
        build_research_step(step_id="s-1", query="")


def test_build_citation_filters_blank_evidence_refs() -> None:
    citation = build_citation(
        citation_id="cit-1",
        finding_ref="finding-7",
        evidence_refs=["ev-a", "", "   ", None, "ev-b"],  # type: ignore[list-item]
    )
    assert citation.evidence_refs == ["ev-a", "ev-b"]


def test_build_citation_requires_nonempty_citation_id_and_finding_ref() -> None:
    with pytest.raises(Exception):
        build_citation(citation_id="", finding_ref="finding-1")
    with pytest.raises(Exception):
        build_citation(citation_id="cit-1", finding_ref="")


# --- verify_claim: structural verdicts ------------------------------------


def _claim(*, required: list[str] | None = None) -> Claim:
    return Claim(
        claim_id="claim-1",
        text="Earth orbits the Sun.",
        required_evidence_kinds=list(required or []),
    )


def test_verify_claim_unsupported_when_no_supporting_evidence() -> None:
    result = verify_claim(_claim(), supporting_evidence_refs=[])
    assert result.status == "unsupported"
    assert result.supporting_evidence_refs == []


def test_verify_claim_unsupported_when_supporting_is_only_blanks() -> None:
    result = verify_claim(_claim(), supporting_evidence_refs=["", "   ", None])  # type: ignore[list-item]
    assert result.status == "unsupported"


def test_verify_claim_supported_when_evidence_present_and_no_required_kinds() -> None:
    result = verify_claim(_claim(), supporting_evidence_refs=["ev-a"])
    assert result.status == "supported"
    assert result.supporting_evidence_refs == ["ev-a"]


def test_verify_claim_contradicted_overrides_supported() -> None:
    result = verify_claim(
        _claim(),
        supporting_evidence_refs=["ev-a", "ev-b", "ev-c"],
        contradicting_evidence_refs=["ev-x"],
    )
    assert result.status == "contradicted"


def test_verify_claim_supported_when_all_required_kinds_covered() -> None:
    result = verify_claim(
        _claim(required=["citation", "fetch_artifact"]),
        supporting_evidence_refs=["ev-a", "ev-b"],
        evidence_kinds_by_ref={"ev-a": "citation", "ev-b": "fetch_artifact"},
    )
    assert result.status == "supported"


def test_verify_claim_indeterminate_when_required_kinds_partially_covered() -> None:
    result = verify_claim(
        _claim(required=["citation", "fetch_artifact"]),
        supporting_evidence_refs=["ev-a"],
        evidence_kinds_by_ref={"ev-a": "citation"},  # missing fetch_artifact
    )
    assert result.status == "indeterminate"


def test_verify_claim_indeterminate_when_evidence_kinds_unknown() -> None:
    result = verify_claim(
        _claim(required=["citation"]),
        supporting_evidence_refs=["ev-a"],
        evidence_kinds_by_ref={},  # no kind info → can't confirm
    )
    assert result.status == "indeterminate"


def test_verify_claim_carries_policy_id_into_result() -> None:
    result = verify_claim(
        _claim(),
        supporting_evidence_refs=["ev-a"],
        policy_id="default-source-trust-v1",
    )
    assert result.policy_id == "default-source-trust-v1"


def test_verify_claim_status_set_is_closed() -> None:
    closed_set = {"supported", "unsupported", "contradicted", "indeterminate"}

    cases = [
        ([], [], [], {}),
        (["ev-a"], [], [], {}),
        (["ev-a"], ["ev-x"], [], {}),
        (["ev-a"], [], ["citation"], {}),
        (["ev-a"], [], ["citation"], {"ev-a": "citation"}),
    ]
    for supporting, contradicting, required, kinds in cases:
        result = verify_claim(
            _claim(required=required),
            supporting_evidence_refs=supporting,
            contradicting_evidence_refs=contradicting,
            evidence_kinds_by_ref=kinds,
        )
        assert result.status in closed_set


# --- Research composition view --------------------------------------------


def test_build_research_composition_combines_typed_pieces() -> None:
    plan = ResearchPlan(
        plan_id="plan-1",
        objective="understand topic X",
        steps=[
            build_research_step(step_id="s-1", query="what is X"),
            build_research_step(step_id="s-2", query="how does X compare to Y"),
        ],
    )
    citations = [
        build_citation(citation_id="cit-1", finding_ref="f-1", evidence_refs=["ev-a"]),
    ]
    verifications = [
        verify_claim(_claim(), supporting_evidence_refs=["ev-a"]),
    ]
    composition = build_research_composition(
        plan=plan,
        citations=citations,
        verifications=verifications,
        findings_count=2,
        evidence_refs_count=3,
    )
    assert composition.plan.plan_id == "plan-1"
    assert len(composition.plan.steps) == 2
    assert composition.findings_count == 2
    assert composition.evidence_refs_count == 3
    assert composition.verifications[0].status == "supported"


def test_build_research_composition_clamps_negative_counts_to_zero() -> None:
    plan = ResearchPlan(
        plan_id="p-1",
        objective="o",
        steps=[build_research_step(step_id="s", query="q")],
    )
    composition = build_research_composition(
        plan=plan, findings_count=-5, evidence_refs_count=-1
    )
    assert composition.findings_count == 0
    assert composition.evidence_refs_count == 0


# --- Anti-LLM discipline regressions --------------------------------------


def test_schemas_do_not_expose_credibility_or_style_fields() -> None:
    forbidden_substrings = (
        "credibility",
        "trust_score",
        "style",
        "narrative",
        "summary",
        "appears_",
        "looks_",
    )
    schema_fields = (
        set(ResearchStep.model_fields.keys())
        | set(ResearchPlan.model_fields.keys())
        | set(Citation.model_fields.keys())
        | set(Claim.model_fields.keys())
        | set(ClaimVerificationResult.model_fields.keys())
        | set(ResearchComposition.model_fields.keys())
    )
    for field_name in schema_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in field_name, (
                f"ROCC discipline violation: schema field {field_name!r} "
                f"contains forbidden substring {forbidden!r}."
            )


def test_verify_claim_does_not_consume_text() -> None:
    typed_claim_a = Claim(claim_id="c-1", text="Earth orbits the Sun.")
    typed_claim_b = Claim(claim_id="c-1", text="completely different prose here")
    a = verify_claim(typed_claim_a, supporting_evidence_refs=["ev-a"])
    b = verify_claim(typed_claim_b, supporting_evidence_refs=["ev-a"])
    assert a.status == b.status == "supported"
    assert a.supporting_evidence_refs == b.supporting_evidence_refs
