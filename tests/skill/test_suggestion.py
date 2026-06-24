from __future__ import annotations

from pathlib import Path


from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.queue import (
    apply_proposal,
    create_proposal,
    record_proposal_review,
)
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.skill.suggestion import (
    DISMISS_REASON_COOLDOWN_ACTIVE,
    DISMISS_REASON_STRUCTURAL_DUPLICATE,
    SkillProposalSuggestion,
    SkillSuggestionStatus,
    list_active_suggestions,
    proposal_signature,
    run_suggestion_surface_pass,
    suggestion_status,
)


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _proposal(
    *,
    proposal_id: str = "scsp-1",
    name: str = "research-latest-news-playbook",
    tags: tuple[str, ...] = ("research_strategy", "live_information", "latest_news"),
    intents: tuple[str, ...] = ("latest_news",),
) -> SkillProposal:
    return SkillProposal(
        proposal_id=proposal_id,
        source_task_shape_ref=(
            f"task_shape:{name}|live_information|{intents[0] if intents else 'na'}"
        ),
        proposed_skill_definition=SkillProposalDraft(
            name=name,
            display_name=name.replace("-", " ").title(),
            short_description="From SCSP test.",
            tools=[],
            tags=list(tags),
            risk_class="low",
            applies_to={"intents": list(intents), "steps": []},
            inputs_schema=[],
            verification_rules=[],
        ),
        evidence_refs=[],
        proposer_policy_id="skill_promotion_cadence_v1",
        proposed_at="",
    )


def test_proposal_signature_is_deterministic_and_non_empty() -> None:
    a = proposal_signature(_proposal(proposal_id="a"))
    b = proposal_signature(_proposal(proposal_id="b"))
    assert a == b
    assert all(part for part in a)


def test_surface_pass_emits_suggestion_for_single_pending_proposal(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        pass_report = run_suggestion_surface_pass(
            store, now="2026-05-26T12:00:00+00:00"
        )
        assert len(pass_report.surfaced) == 1
        suggestion = pass_report.surfaced[0]
        assert isinstance(suggestion, SkillProposalSuggestion)
        assert suggestion.proposal_id == "scsp-1"
        # Anti-LLM guardrail: no quality/score/confidence field.
        d = suggestion.to_dict()
        for forbidden in ("quality", "value", "score", "confidence"):
            assert forbidden not in d
        assert suggestion.cli_inspect_command.endswith("scsp-1")
    finally:
        store.close()


def test_surface_pass_dedupes_within_same_pass_by_structural_signature(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        # Two proposals with the same structural signature (same name, tags,
        # intents). The second is auto-dismissed at surface time.
        create_proposal(store, _proposal(proposal_id="scsp-a"))
        create_proposal(store, _proposal(proposal_id="scsp-b"))
        pass_report = run_suggestion_surface_pass(
            store, now="2026-05-26T12:00:00+00:00"
        )
        assert len(pass_report.surfaced) == 1
        assert len(pass_report.auto_dismissed) == 1
        assert (
            pass_report.auto_dismissed[0]["reason"]
            == DISMISS_REASON_STRUCTURAL_DUPLICATE
        )
    finally:
        store.close()


def test_surface_pass_respects_cooldown_window_across_passes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal(proposal_id="scsp-cool-1"))
        first = run_suggestion_surface_pass(
            store,
            now="2026-05-26T12:00:00+00:00",
            cooldown_seconds=3600,
        )
        assert len(first.surfaced) == 1

        # Same signature, distinct proposal id, within cooldown window.
        create_proposal(
            store,
            _proposal(proposal_id="scsp-cool-2"),
        )
        second = run_suggestion_surface_pass(
            store,
            now="2026-05-26T12:10:00+00:00",
            cooldown_seconds=3600,
        )
        # Structural behavior: the cooldown is per-signature, not per-proposal.
        # Both pending proposals with this signature are auto-dismissed during
        # the cooldown window (anti-spam: do not re-surface the same signal).
        assert second.surfaced == []
        assert len(second.auto_dismissed) >= 1
        for entry in second.auto_dismissed:
            assert entry["reason"] == DISMISS_REASON_COOLDOWN_ACTIVE

        third = run_suggestion_surface_pass(
            store,
            now="2026-05-26T15:00:00+00:00",
            cooldown_seconds=3600,
        )
        assert len(third.surfaced) == 1
    finally:
        store.close()


def test_surface_pass_respects_batch_cap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        # Create 4 proposals with distinct signatures (distinct names).
        for idx in range(4):
            create_proposal(
                store,
                _proposal(
                    proposal_id=f"scsp-cap-{idx}",
                    name=f"playbook-distinct-{idx}",
                    tags=(f"capability-{idx}",),
                    intents=(f"intent-{idx}",),
                ),
            )
        pass_report = run_suggestion_surface_pass(
            store,
            now="2026-05-26T12:00:00+00:00",
            batch_cap=2,
        )
        assert len(pass_report.surfaced) == 2
        assert pass_report.auto_dismissed == []
        # The remaining proposals are still pending — not auto-dismissed.
        remaining = list_active_suggestions(store)
        assert len(remaining) == 4
    finally:
        store.close()


def test_surface_pass_respects_min_age(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        pass_report = run_suggestion_surface_pass(
            store,
            min_age_seconds=3600,
        )
        # Not yet eligible, no audit row written.
        assert pass_report.surfaced == []
        assert pass_report.auto_dismissed == []
    finally:
        store.close()


def test_list_active_suggestions_returns_pending_view_without_audit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal(proposal_id="scsp-x"))
        suggestions = list_active_suggestions(store)
        assert len(suggestions) == 1
        assert suggestions[0].proposal_id == "scsp-x"
        # Read-only view did NOT generate audit rows.
        status = suggestion_status(store)
        assert status.surfaced_count == 0
        assert status.auto_dismissed_count == 0
        assert status.pending_count == 1
    finally:
        store.close()


def test_suggestion_status_reflects_audit_counts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal(proposal_id="scsp-status-a"))
        create_proposal(
            store,
            _proposal(
                proposal_id="scsp-status-b",
                name="distinct-playbook-2",
                tags=("capability-b",),
                intents=("intent-b",),
            ),
        )
        run_suggestion_surface_pass(store, now="2026-05-26T12:00:00+00:00")

        # Apply one review (accepted).
        record_proposal_review(
            store,
            proposal_id="scsp-status-a",
            reviewer_id="operator-scsp",
            review_policy_id="scsp_policy_v1",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "ok",
                },
            ],
        )

        status = suggestion_status(store)
        assert isinstance(status, SkillSuggestionStatus)
        assert status.surfaced_count == 2
        assert status.accepted_count == 1
        assert status.rejected_count == 0
        assert status.deferred_count == 0
        assert status.pending_count == 1  # scsp-status-b still pending
        assert status.last_surfaced_at
        assert status.last_outcome_at
    finally:
        store.close()


def test_outcome_audit_written_on_review_landing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="scsp-1",
            reviewer_id="operator-scsp",
            review_policy_id="scsp",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "rejected",
                    "comment": "no",
                },
            ],
        )
        counts = store.count_suggestion_events()
        assert counts["rejected_count"] == 1
        assert counts["accepted_count"] == 0
    finally:
        store.close()


def test_outcome_audit_after_apply_does_not_double_count(tmp_path: Path) -> None:

    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="scsp-1",
            reviewer_id="operator-scsp",
            review_policy_id="scsp",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "ok",
                },
            ],
        )
        apply_proposal(store, proposal_id="scsp-1", current_catalog=[])
        status = suggestion_status(store)
        assert status.accepted_count == 1
        assert status.rejected_count == 0
        assert status.deferred_count == 0
    finally:
        store.close()


def test_suggestion_module_has_no_quality_classifier_function() -> None:

    import ast
    from openminion.modules.skill import suggestion as suggestion_module

    source = Path(suggestion_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "classify_proposal",
        "score_proposal",
        "judge_proposal",
        "is_worth_showing",
        "is_high_quality",
        "rank_proposals",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in forbidden, (
                f"suggestion.py defines forbidden classifier {node.name!r}"
            )
