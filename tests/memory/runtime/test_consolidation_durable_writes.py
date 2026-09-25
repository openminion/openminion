from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.memory.errors import InvalidArgumentError, PromotionDeniedError
from openminion.modules.memory.models import (
    CandidateReview,
    MemoryCandidate,
    MemoryRecord,
)
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ExtractionPayload,
    MergeDecision,
    MergeDecisions,
)
from openminion.modules.memory.runtime.consolidation.merge import (
    apply_merge_decisions_via_service,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.storage.sqlite import write as sqlite_write
from openminion.modules.memory.storage.audit import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate(candidate_id: str, **overrides: object) -> MemoryCandidate:
    payload: dict[str, object] = {
        "candidate_id": candidate_id,
        "session_id": "session-1",
        "proposed_scope": "agent:test",
        "type": "fact",
        "title": f"title-{candidate_id}",
        "content": {"text": f"content-{candidate_id}"},
        "source": "validated",
        "confidence": 0.8,
        "meta": {"existing": "preserved"},
        "created_at": _now(),
        "updated_at": _now(),
    }
    payload.update(overrides)
    return MemoryCandidate(**payload)


def _payload() -> ExtractionPayload:
    return ExtractionPayload(
        session_id="session-1",
        agent_id="agent-1",
        candidate_refs=[
            {"candidate_id": "cand-promote"},
            {"candidate_id": "cand-discard"},
            {"candidate_id": "cand-defer"},
            {"candidate_id": "cand-keep"},
        ],
        duplicate_hints=[
            {
                "candidate_id": "cand-promote",
                "existing_record_id": "old-1",
            }
        ],
        evidence_window={"recent_rollout_limit": 256},
    )


class _FakeMemoryService:
    def __init__(self, *, blocked_ids: set[str] | None = None) -> None:
        self.candidates = {
            "cand-promote": _candidate("cand-promote"),
            "cand-discard": _candidate("cand-discard"),
            "cand-defer": _candidate("cand-defer"),
            "cand-keep": _candidate("cand-keep"),
            "cand-blocked": _candidate("cand-blocked"),
        }
        self.blocked_ids = set(blocked_ids or set())
        self.update_calls: list[tuple[str, dict[str, object]]] = []
        self.promote_calls: list[tuple[str, str]] = []
        self.supersede_calls: list[tuple[str, str, str]] = []
        self._store = SimpleNamespace(
            write=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("direct store write bypass is forbidden")
            ),
            promote_candidate=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("direct store promote bypass is forbidden")
            ),
            candidate_update=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("direct store candidate_update bypass is forbidden")
            ),
        )

    def candidate_get(self, candidate_id: str) -> MemoryCandidate:
        return self.candidates[candidate_id]

    def candidate_update(
        self, candidate_id: str, patch: dict[str, object]
    ) -> MemoryCandidate:
        current = self.candidates[candidate_id]
        updated = replace(
            current,
            status=str(patch.get("status", current.status)),
            review=patch.get("review", current.review),
            meta=dict(patch.get("meta", current.meta)),
            updated_at=str(patch.get("updated_at", _now())),
        )
        self.candidates[candidate_id] = updated
        self.update_calls.append((candidate_id, dict(patch)))
        return updated

    def apply_consolidation_decision(
        self,
        candidate: MemoryCandidate,
        *,
        action: str,
        target_scope: str,
        review: object,
        meta: dict[str, object],
    ) -> MemoryCandidate | MemoryRecord:
        if action == "promote" and candidate.candidate_id in self.blocked_ids:
            raise PromotionDeniedError("blocked by trust gate")
        patch: dict[str, object] = {"review": review, "meta": meta}
        if action == "discard":
            patch["status"] = "rejected"
        updated = self.candidate_update(candidate.candidate_id, patch)
        if action == "promote":
            self.promote_calls.append((candidate.candidate_id, target_scope))
            return MemoryRecord(
                id=f"mem-promoted-{candidate.candidate_id}",
                scope=target_scope,
                type="fact",
                content={"text": f"promoted-{candidate.candidate_id}"},
                created_at=_now(),
                updated_at=_now(),
            )
        return updated

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        self.supersede_calls.append((old_record_id, new_record_id, reason))
        now = _now()
        return MemoryRecord(
            id=new_record_id,
            scope="agent:test",
            type="fact",
            content={"text": "updated"},
            created_at=now,
            updated_at=now,
            supersedes_id=old_record_id,
        )


def test_apply_merge_decisions_routes_each_action_via_service() -> None:
    service = _FakeMemoryService()
    result = apply_merge_decisions_via_service(
        service,
        payload=_payload(),
        merge_decisions=MergeDecisions(
            decisions=[
                MergeDecision(
                    candidate_id="cand-promote",
                    action="promote",
                    reasoning="durable lesson",
                ),
                MergeDecision(
                    candidate_id="cand-discard",
                    action="discard",
                    reasoning="low value",
                ),
                MergeDecision(
                    candidate_id="cand-defer",
                    action="defer",
                    reasoning="need more evidence",
                ),
                MergeDecision(
                    candidate_id="cand-keep",
                    action="keep",
                    reasoning="already represented",
                ),
            ]
        ),
        target_scope="agent:test",
    )

    assert result["applied_count"] == 4
    assert result["promoted_count"] == 1
    assert result["discarded_count"] == 1
    assert result["deferred_count"] == 1
    assert result["kept_count"] == 1
    assert result["errors"] == []
    assert [candidate_id for candidate_id, _ in service.update_calls] == [
        "cand-promote",
        "cand-discard",
        "cand-defer",
    ]
    assert service.promote_calls == [("cand-promote", "agent:test")]
    assert service.supersede_calls == [
        ("old-1", "mem-promoted-cand-promote", "durable lesson")
    ]
    assert service.candidates["cand-promote"].status == "proposed"
    assert service.candidates["cand-discard"].status == "rejected"
    assert service.candidates["cand-defer"].status == "proposed"
    assert service.candidates["cand-keep"].status == "proposed"
    assert service.candidates["cand-defer"].meta["existing"] == "preserved"
    assert service.candidates["cand-defer"].meta["consolidation_action"] == "defer"


def test_apply_merge_decisions_keeps_blocked_candidate_in_pool() -> None:
    service = _FakeMemoryService(blocked_ids={"cand-blocked"})
    result = apply_merge_decisions_via_service(
        service,
        payload=ExtractionPayload(
            session_id="session-1",
            agent_id="agent-1",
            candidate_refs=[{"candidate_id": "cand-blocked"}],
            evidence_window={"recent_rollout_limit": 256},
        ),
        merge_decisions=MergeDecisions(
            decisions=[
                MergeDecision(
                    candidate_id="cand-blocked",
                    action="promote",
                    reasoning="try promote",
                )
            ]
        ),
        target_scope="agent:test",
    )

    assert result["applied_count"] == 0
    assert result["promoted_count"] == 0
    assert len(result["errors"]) == 1
    assert "cand-blocked" in result["errors"][0]
    assert service.candidates["cand-blocked"].status == "proposed"
    assert service.candidates["cand-blocked"].meta["existing"] == "preserved"
    assert "consolidation_action" not in service.candidates["cand-blocked"].meta


def test_apply_merge_decisions_sets_valid_to_on_superseded_record() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    old_record = MemoryRecord(
        id="old-1",
        scope="agent:test",
        type="fact",
        content={"text": "old fact"},
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
        confidence=0.6,
    )
    store.put(old_record)
    store.candidate_put(
        _candidate(
            "cand-promote",
            status="proposed",
            source="validated",
            meta={},
        )
    )

    result = apply_merge_decisions_via_service(
        service,
        payload=ExtractionPayload(
            session_id="session-1",
            agent_id="agent-1",
            candidate_refs=[{"candidate_id": "cand-promote"}],
            contradiction_hints=[
                {
                    "candidate_id": "cand-promote",
                    "record_id": "old-1",
                    "record_is_current": True,
                }
            ],
            evidence_window={"recent_rollout_limit": 256},
        ),
        merge_decisions=MergeDecisions(
            decisions=[
                MergeDecision(
                    candidate_id="cand-promote",
                    action="promote",
                    reasoning="contradicts prior fact",
                )
            ]
        ),
        target_scope="agent:test",
    )

    promoted_id = result["promoted_record_ids"][0]
    old_after = store.get("old-1")
    promoted_after = store.get(promoted_id)

    assert result["promoted_count"] == 1
    assert old_after is not None
    assert promoted_after is not None
    assert old_after.valid_to is not None
    assert old_after.superseded_by_id == promoted_id
    assert promoted_after.supersedes_id == "old-1"


@pytest.fixture(params=["memory", "sqlite"])
def conditional_store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "memory":
        return InMemoryMemoryStore()
    return SQLiteMemoryStore(tmp_path / "memory.db")


def _review() -> CandidateReview:
    return CandidateReview(
        reviewer="memory_consolidation",
        decided_at="2026-09-25T00:00:00+00:00",
        note="reviewed",
    )


def test_conditional_promotion_commits_directly_from_proposed(
    conditional_store,
) -> None:
    candidate = _candidate("cand-promote", source="validated", status="proposed")
    conditional_store.candidate_put(candidate)
    service = MemoryService(store=conditional_store)

    record = service.apply_consolidation_decision(
        service.candidate_get(candidate.candidate_id),
        action="promote",
        target_scope="agent:test",
        review=_review(),
        meta={"consolidation_action": "promote"},
    )

    promoted = conditional_store.candidate_get(candidate.candidate_id)
    assert promoted is not None
    assert promoted.status == "promoted"
    assert promoted.review == _review()
    assert promoted.meta["consolidation_action"] == "promote"
    assert record.scope == "agent:test"
    assert service.last_policy_decisions()[-1]["allowed"] is True


def test_conditional_promotion_denial_leaves_candidate_unchanged(
    conditional_store,
) -> None:
    candidate = _candidate("cand-denied", source="agent_inferred", status="proposed")
    conditional_store.candidate_put(candidate)
    service = MemoryService(store=conditional_store)
    before = service.candidate_get(candidate.candidate_id)

    with pytest.raises(PromotionDeniedError):
        service.apply_consolidation_decision(
            before,
            action="promote",
            target_scope="agent:test",
            review=_review(),
            meta={"consolidation_action": "promote"},
        )

    assert service.candidate_get(candidate.candidate_id) == before
    assert conditional_store.list(ListQueryOptions(scopes=["agent:test"])) == []
    assert service.last_policy_decisions()[-1]["allowed"] is False


def test_conditional_promotion_rejects_scope_before_policy(conditional_store) -> None:
    candidate = _candidate(
        "cand-other-scope",
        proposed_scope="agent:other",
        source="agent_inferred",
        status="proposed",
    )
    conditional_store.candidate_put(candidate)
    service = MemoryService(store=conditional_store)

    with pytest.raises(InvalidArgumentError, match="outside the active"):
        service.apply_consolidation_decision(
            service.candidate_get(candidate.candidate_id),
            action="promote",
            target_scope="agent:test",
            review=_review(),
            meta={"consolidation_action": "promote"},
        )

    assert service.candidate_get(candidate.candidate_id) == candidate
    assert service.last_policy_decisions() == []


def test_conditional_decision_rejects_stale_snapshot(conditional_store) -> None:
    candidate = _candidate("cand-stale", source="validated", status="proposed")
    conditional_store.candidate_put(candidate)
    service = MemoryService(store=conditional_store)
    stale = service.candidate_get(candidate.candidate_id)
    service.candidate_update(candidate.candidate_id, {"content": {"text": "changed"}})

    with pytest.raises(InvalidArgumentError, match="changed before consolidation"):
        service.apply_consolidation_decision(
            stale,
            action="promote",
            target_scope="agent:test",
            review=_review(),
            meta={"consolidation_action": "promote"},
        )

    assert service.candidate_get(candidate.candidate_id).status == "proposed"
    assert conditional_store.list(ListQueryOptions(scopes=["agent:test"])) == []
    assert service.last_policy_decisions() == []


def test_sqlite_conditional_promotion_rolls_back_after_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteMemoryStore(tmp_path / "rollback.db")
    service = MemoryService(store=store)
    candidate = _candidate("cand-rollback", source="validated", status="proposed")
    store.candidate_put(candidate)
    original_insert = sqlite_write._insert_promoted_candidate

    def fail_after_insert(*args, **kwargs):
        original_insert(*args, **kwargs)
        raise RuntimeError("induced promotion failure")

    monkeypatch.setattr(sqlite_write, "_insert_promoted_candidate", fail_after_insert)
    with pytest.raises(RuntimeError, match="induced promotion failure"):
        service.apply_consolidation_decision(
            service.candidate_get(candidate.candidate_id),
            action="promote",
            target_scope="agent:test",
            review=_review(),
            meta={"consolidation_action": "promote"},
        )

    assert store.candidate_get(candidate.candidate_id).status == "proposed"
    assert store.list(ListQueryOptions(scopes=["agent:test"])) == []
    assert service.last_policy_decisions() == []


def test_terminal_candidate_rejects_stale_writer(conditional_store) -> None:
    candidate = _candidate("cand-terminal", source="validated", status="proposed")
    conditional_store.candidate_put(candidate)
    service = MemoryService(store=conditional_store)
    stale = service.candidate_get(candidate.candidate_id)
    service.apply_consolidation_decision(
        stale,
        action="promote",
        target_scope="agent:test",
        review=_review(),
        meta={"consolidation_action": "promote"},
    )

    with pytest.raises(
        InvalidArgumentError, match="cannot regress from terminal status"
    ):
        conditional_store.candidate_put(stale)
    with pytest.raises(
        InvalidArgumentError, match="cannot regress from terminal status"
    ):
        conditional_store.candidate_update(
            candidate.candidate_id, {"status": "proposed"}
        )
    assert conditional_store.candidate_get(candidate.candidate_id).status == "promoted"


def test_in_memory_candidates_detach_nested_values() -> None:
    store = InMemoryMemoryStore()
    candidate = _candidate("cand-alias", meta={"nested": {"value": "original"}})
    store.candidate_put(candidate)
    candidate.meta["nested"]["value"] = "input-mutated"
    loaded = store.candidate_get(candidate.candidate_id)
    assert loaded is not None
    loaded.meta["nested"]["value"] = "read-mutated"

    assert store.candidate_get(candidate.candidate_id).meta["nested"] == {
        "value": "original"
    }
    service = MemoryService(store=store)
    with pytest.raises(InvalidArgumentError, match="changed before consolidation"):
        service.apply_consolidation_decision(
            loaded,
            action="promote",
            target_scope="agent:test",
            review=_review(),
            meta={"consolidation_action": "promote"},
        )
    assert store.list(ListQueryOptions(scopes=["agent:test"])) == []


def test_conditional_consolidation_audits_only_committed_write() -> None:
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(InMemoryMemoryStore(), sink=sink)
    service = MemoryService(store=store)
    candidate = _candidate("cand-audit", source="validated", status="proposed")
    store.candidate_put(candidate)
    snapshot = service.candidate_get(candidate.candidate_id)
    sink.events.clear()

    service.apply_consolidation_decision(
        snapshot,
        action="promote",
        target_scope="agent:test",
        review=_review(),
        meta={"consolidation_action": "promote"},
    )

    assert [event.event_type for event in sink.events] == ["memory.candidate.promote"]


@pytest.mark.parametrize(
    ("action", "patched_fields"),
    [("defer", ["meta", "review"]), ("discard", ["meta", "review", "status"])],
)
def test_conditional_consolidation_audit_reports_changed_fields(
    action: str, patched_fields: list[str]
) -> None:
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(InMemoryMemoryStore(), sink=sink)
    service = MemoryService(store=store)
    candidate = _candidate(f"cand-audit-{action}", status="proposed")
    store.candidate_put(candidate)
    snapshot = service.candidate_get(candidate.candidate_id)
    sink.events.clear()

    service.apply_consolidation_decision(
        snapshot,
        action=action,
        target_scope="agent:test",
        review=_review(),
        meta={"consolidation_action": action},
    )

    assert len(sink.events) == 1
    assert sink.events[0].details["patched_fields"] == patched_fields
