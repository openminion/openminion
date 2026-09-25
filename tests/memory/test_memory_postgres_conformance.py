from __future__ import annotations

import datetime
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

import pytest
import sqlalchemy as sa

from openminion.modules.memory.models import (
    CandidateReview,
    MemoryCandidate,
    MemoryRecord,
)
from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureBundleIntegrityError,
    CaptureCandidateInput,
)
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ExtractionPayload,
    MergeDecision,
    MergeDecisions,
)
from openminion.modules.memory.runtime.consolidation.merge import (
    apply_merge_decisions_via_service,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.postgres import (
    candidate_supersession as postgres_candidate_supersession,
)
from openminion.modules.memory.storage.postgres import write as postgres_write
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.errors import InvalidArgumentError, PromotionDeniedError
from openminion.modules.memory.storage.audit import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _record(
    record_id: str, *, scope: str = "session:s1", key: str | None = None
) -> MemoryRecord:
    now = _now()
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        key=key,
        title=f"title-{record_id}",
        content={"text": f"content-{record_id}"},
        tags=["alpha"],
        entities=["Alice"],
        created_at=now,
        updated_at=now,
    )


def _candidate(candidate_id: str, *, status: str = "proposed") -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="s1",
        proposed_scope="session:s1",
        type="fact",
        content={"text": f"candidate-{candidate_id}"},
        status=status,
    )


@pytest.fixture(params=["sqlite", "postgres"], ids=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "sqlite":
        with tempfile.TemporaryDirectory() as tmp:
            yield SQLiteMemoryStore(Path(tmp) / "memory.db")
        return

    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"mpt3_memory_{datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S%f')}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sa.create_engine(schema_url(postgres_url, schema_name), future=True)
    try:
        yield PostgresMemoryStore(
            engine,
            database_path=(
                tmp_path / ".openminion" / "memory" / "postgres-conformance.db"
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_non_search_conformance_round_trip(store) -> None:
    first = _record("r1", key="theme")
    second = _record("r2", scope="session:s2")
    store.put(first)
    store.put(second)

    listed = store.list(ListQueryOptions(scopes=["session:s1", "session:s2"]))
    assert {item.id for item in listed} == {"r1", "r2"}

    upserted = store.upsert(
        "session:s1", "fact", "theme", {"content": {"text": "updated"}}
    )
    assert upserted.supersedes_id == "r1"

    history = store.history("session:s1", "fact", "theme")
    assert [item.id for item in history[:2]] == [upserted.id, "r1"]

    before_touch = store.get(upserted.id)
    assert before_touch is not None
    store.touch_last_hit(upserted.id)
    store.touch_last_hit(upserted.id)
    after_touch = store.get(upserted.id)
    assert after_touch is not None
    assert after_touch.access_count == before_touch.access_count + 2
    assert after_touch.confidence == before_touch.confidence
    assert after_touch.meta == before_touch.meta
    assert after_touch.tier == before_touch.tier
    updated = store.apply_outcome_feedback(
        [upserted.id],
        outcome="success",
        command_id="cmd-1",
        observed_at=_now(),
        feedback_delta=0.3,
    )
    assert updated == 1

    entity_hits = store.retrieve_by_entities(["Alice"], ["session:s1", "session:s2"])
    assert {item.id for item in entity_hits} >= {upserted.id, "r2"}

    store.delete("r2")
    after_delete = store.list(ListQueryOptions(scopes=["session:s1", "session:s2"]))
    assert {item.id for item in after_delete} == {upserted.id}


def test_candidate_and_promotion_conformance_round_trip(store) -> None:
    candidate = _candidate("c1")
    store.candidate_put(candidate)
    reviewed = store.candidate_update(
        "c1",
        {
            "status": "approved",
            "review": CandidateReview(
                reviewer="agent",
                decided_at="2026-04-01T00:00:00+00:00",
                note="approved",
            ),
        },
    )
    assert reviewed.status == "approved"
    listed = store.candidate_list(
        CandidateListOptions(session_id="s1", status="approved")
    )
    assert [item.candidate_id for item in listed] == ["c1"]
    promoted = store.promote_candidate("c1", "agent:main")
    assert promoted.scope == "agent:main"


def test_checked_consolidation_supersession_conformance(store) -> None:
    old_record = _record("hint-old", scope="agent:main")
    new_record = _record("hint-new", scope="agent:main")
    cross_scope_record = _record("hint-other", scope="agent:other")
    store.put(old_record)
    store.put(new_record)
    store.put(cross_scope_record)
    service = MemoryService(store=store)

    superseding = service.supersede_consolidation_hint(
        old_record.id,
        new_record.id,
        target_scope="agent:main",
        reason="consolidation hint",
    )

    assert superseding.supersedes_id == old_record.id
    assert store.get(old_record.id).superseded_by_id == new_record.id
    with pytest.raises(InvalidArgumentError, match="stale or outside"):
        service.supersede_consolidation_hint(
            cross_scope_record.id,
            new_record.id,
            target_scope="agent:main",
            reason="cross-scope hint",
        )
    assert store.get(cross_scope_record.id).superseded_by_id is None


def test_keyed_duplicate_hint_is_idempotent_after_promotion(store) -> None:
    sink = InMemoryMemoryAuditSink()
    audited = AuditedMemoryStore(store, sink=sink)
    service = MemoryService(store=audited)
    old_record = _record("hint-keyed-old", scope="agent:main", key="fact:keyed")
    audited.put(old_record)
    audited.candidate_put(
        MemoryCandidate(
            candidate_id="hint-keyed-candidate",
            session_id="s1",
            proposed_scope="agent:main",
            type="fact",
            key="fact:keyed",
            content={"text": "new keyed fact"},
            source="validated",
            status="proposed",
        )
    )
    sink.events.clear()

    result = apply_merge_decisions_via_service(
        service,
        payload=ExtractionPayload(
            session_id="run-keyed",
            agent_id="main",
            candidate_refs=[{"candidate_id": "hint-keyed-candidate"}],
            duplicate_hints=[
                {
                    "candidate_id": "hint-keyed-candidate",
                    "existing_record_id": old_record.id,
                }
            ],
        ),
        merge_decisions=MergeDecisions(
            decisions=[
                MergeDecision(
                    candidate_id="hint-keyed-candidate",
                    action="promote",
                )
            ]
        ),
        target_scope="agent:main",
    )

    promoted_id = result["promoted_record_ids"][0]
    old_after = audited.get(old_record.id)
    promoted_after = audited.get(promoted_id)
    assert result["promoted_count"] == 1
    assert result["supersession_errors"] == []
    assert result["superseded_record_ids"] == [promoted_id]
    assert old_after.superseded_by_id == promoted_id
    assert old_after.updated_at == promoted_after.created_at
    assert promoted_after.supersedes_id == old_record.id
    assert [event.event_type for event in sink.events] == [
        "memory.candidate.promote",
        "memory.record.supersede",
    ]


def test_postgres_checked_hint_waits_and_rejects_committed_stale_record(
    tmp_path: Path,
) -> None:
    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"memory_hint_contention_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
    engines = [
        sa.create_engine(schema_url(postgres_url, schema_name), future=True)
        for _ in range(2)
    ]
    try:
        stores = [
            PostgresMemoryStore(
                engine,
                database_path=tmp_path / f"memory-hint-{index}.db",
                artifactctl=None,
            )
            for index, engine in enumerate(engines)
        ]
        old_record = _record("hint-a-old", scope="agent:main")
        new_record = _record("hint-z-new", scope="agent:main")
        stores[0].put(old_record)
        stores[0].put(new_record)
        started = threading.Event()
        errors: list[Exception] = []

        def apply_hint() -> None:
            started.set()
            try:
                MemoryService(store=stores[1]).supersede_consolidation_hint(
                    old_record.id,
                    new_record.id,
                    target_scope="agent:main",
                )
            except Exception as exc:
                errors.append(exc)

        with engines[0].begin() as conn:
            conn.execute(
                sa.text("SELECT id FROM memory_records WHERE id = :id FOR UPDATE"),
                {"id": old_record.id},
            )
            hint = threading.Thread(target=apply_hint)
            hint.start()
            assert started.wait(timeout=2)
            time.sleep(0.1)
            assert hint.is_alive()
            conn.execute(
                sa.text(
                    "UPDATE memory_records SET valid_to = :valid_to WHERE id = :id"
                ),
                {"id": old_record.id, "valid_to": "2026-01-01T00:00:00+00:00"},
            )
        hint.join(timeout=5)

        assert not hint.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], InvalidArgumentError)
        assert "stale or outside" in str(errors[0])
        assert stores[0].get(old_record.id).superseded_by_id is None
        assert stores[0].get(new_record.id).supersedes_id is None
    finally:
        for engine in engines:
            engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_postgres_consolidation_serializes_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"memory_consolidation_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
    engines = [
        sa.create_engine(schema_url(postgres_url, schema_name), future=True)
        for _ in range(2)
    ]
    try:
        stores = [
            PostgresMemoryStore(
                engine,
                database_path=tmp_path / f"memory-consolidation-{index}.db",
                artifactctl=None,
            )
            for index, engine in enumerate(engines)
        ]
        candidate = MemoryCandidate(
            candidate_id="candidate-consolidation",
            session_id="s1",
            proposed_scope="agent:main",
            type="fact",
            content={"text": "candidate"},
            source="validated",
            status="proposed",
        )
        stores[0].candidate_put(candidate)
        snapshots = [store.candidate_get(candidate.candidate_id) for store in stores]
        original_insert = postgres_candidate_supersession._insert_promoted_candidate
        first_locked = threading.Event()
        calls_lock = threading.Lock()
        calls = 0

        def delayed_insert(*args, **kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
                is_first = calls == 1
            if is_first:
                first_locked.set()
                time.sleep(0.2)
            return original_insert(*args, **kwargs)

        monkeypatch.setattr(
            postgres_candidate_supersession,
            "_insert_promoted_candidate",
            delayed_insert,
        )
        review = CandidateReview(
            reviewer="memory_consolidation",
            decided_at="2026-09-25T00:00:00+00:00",
            note="reviewed",
        )
        promoted: list[MemoryRecord] = []
        errors: list[Exception] = []

        def apply(store: PostgresMemoryStore, snapshot: MemoryCandidate) -> None:
            try:
                result = MemoryService(store=store).apply_consolidation_decision(
                    snapshot,
                    action="promote",
                    target_scope="agent:main",
                    review=review,
                    meta={"consolidation_action": "promote"},
                )
                assert isinstance(result, MemoryRecord)
                promoted.append(result)
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=apply, args=(stores[0], snapshots[0]))
        second = threading.Thread(target=apply, args=(stores[1], snapshots[1]))
        first.start()
        assert first_locked.wait(timeout=2)
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive()
        assert not second.is_alive()
        assert len(promoted) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], InvalidArgumentError)
        assert "changed before consolidation" in str(errors[0])
        assert stores[0].candidate_get(candidate.candidate_id).status == "promoted"
        assert len(stores[0].list(ListQueryOptions(scopes=["agent:main"]))) == 1

        sink = InMemoryMemoryAuditSink()
        audited = AuditedMemoryStore(stores[0], sink=sink)
        service = MemoryService(store=audited)
        for action, expected_status, expected_fields in (
            ("defer", "proposed", ["meta", "review"]),
            ("discard", "rejected", ["meta", "review", "status"]),
        ):
            action_candidate = MemoryCandidate(
                candidate_id=f"candidate-{action}",
                session_id="s1",
                proposed_scope=f"agent:{action}",
                type="fact",
                content={"text": f"{action} candidate"},
                source="validated",
                status="proposed",
            )
            audited.candidate_put(action_candidate)
            sink.events.clear()
            service.apply_consolidation_decision(
                service.candidate_get(action_candidate.candidate_id),
                action=action,
                target_scope=f"agent:{action}",
                review=review,
                meta={"consolidation_action": action},
            )
            assert audited.candidate_get(action_candidate.candidate_id).status == (
                expected_status
            )
            assert len(sink.events) == 1
            assert sink.events[0].details["patched_fields"] == expected_fields

        denied = MemoryCandidate(
            candidate_id="candidate-denied",
            session_id="s1",
            proposed_scope="agent:denied",
            type="fact",
            content={"text": "denied candidate"},
            source="agent_inferred",
            status="proposed",
        )
        audited.candidate_put(denied)
        sink.events.clear()
        with pytest.raises(PromotionDeniedError):
            service.apply_consolidation_decision(
                service.candidate_get(denied.candidate_id),
                action="promote",
                target_scope="agent:denied",
                review=review,
                meta={"consolidation_action": "promote"},
            )
        assert audited.candidate_get(denied.candidate_id).status == "proposed"
        assert audited.list(ListQueryOptions(scopes=["agent:denied"])) == []
        assert sink.events == []

        rollback_candidate = MemoryCandidate(
            candidate_id="candidate-rollback",
            session_id="s1",
            proposed_scope="agent:rollback",
            type="fact",
            content={"text": "rollback candidate"},
            source="validated",
            status="proposed",
        )
        stores[0].candidate_put(rollback_candidate)

        def fail_after_insert(*args, **kwargs):
            original_insert(*args, **kwargs)
            raise RuntimeError("induced promotion failure")

        monkeypatch.setattr(
            postgres_candidate_supersession,
            "_insert_promoted_candidate",
            fail_after_insert,
        )
        sink.events.clear()
        with pytest.raises(RuntimeError, match="induced promotion failure"):
            service.apply_consolidation_decision(
                audited.candidate_get(rollback_candidate.candidate_id),
                action="promote",
                target_scope="agent:rollback",
                review=review,
                meta={"consolidation_action": "promote"},
            )
        assert stores[0].candidate_get(rollback_candidate.candidate_id).status == (
            "proposed"
        )
        assert stores[0].list(ListQueryOptions(scopes=["agent:rollback"])) == []
        assert sink.events == []
    finally:
        for engine in engines:
            engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_capture_bundle_conformance_round_trip(store) -> None:
    bundle = CaptureBundleInput(
        capture_id="capture-conformance",
        root_turn_id="turn-conformance",
        session_id="session-conformance",
        agent_id="agent-conformance",
        candidates=(
            CaptureCandidateInput(
                kind="fact",
                normalized_key="fact:conformance",
                title="Conformance fact",
                content="Capture bundles are atomic.",
                confidence=0.9,
            ),
        ),
    )

    first = store.apply_capture_bundle(bundle)
    replay = store.apply_capture_bundle(bundle)

    assert replay == first
    assert first.disposition == "succeeded"
    assert len(first.output_ids) == 1
    assert store.candidate_get(first.output_ids[0]) is not None

    changed = CaptureBundleInput(
        capture_id=bundle.capture_id,
        root_turn_id=bundle.root_turn_id,
        session_id=bundle.session_id,
        agent_id=bundle.agent_id,
        candidates=(
            CaptureCandidateInput(
                kind="fact",
                normalized_key="fact:conformance",
                title="Conformance fact",
                content="Changed content conflicts.",
            ),
        ),
    )
    with pytest.raises(CaptureBundleIntegrityError):
        store.apply_capture_bundle(changed)


def test_invalidate_conformance_round_trip(store) -> None:
    record = _record("r-invalidate", scope="session:s-bti")
    store.put(record)

    updated = store.invalidate(
        record.id,
        valid_to="2026-05-21T00:00:00+00:00",
        reason="corrected",
    )

    assert updated.valid_to == "2026-05-21T00:00:00+00:00"
    active = store.list(ListQueryOptions(scopes=["session:s-bti"]))
    assert active == []
    audit_visible = store.list(
        ListQueryOptions(scopes=["session:s-bti"], include_invalidated=True)
    )
    assert [item.id for item in audit_visible] == [record.id]


def test_relation_conformance_round_trip(store) -> None:
    first = _record("r1")
    second = _record("r2")
    store.put(first)
    store.put(second)

    from openminion.modules.memory.models import MemoryRelation

    relation_id = store.put_relation(
        MemoryRelation(
            relation_id="rel_1",
            source_record_id="r1",
            target_record_id="r2",
            relation_type="supports",
            created_at=_now(),
            meta={"reason": "linked"},
        )
    )
    assert relation_id == "rel_1"

    relations = store.list_relations("r1", relation_types=["supports"])
    assert len(relations) == 1
    assert relations[0].target_record_id == "r2"

    related = store.get_related_records(
        "r1",
        scopes=["session:s1"],
        relation_types=["supports"],
    )
    assert [item.id for item in related] == ["r2"]


def test_feedback_is_once_per_command_across_postgres_store_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    schema_name = f"memory_feedback_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
    engines = [
        sa.create_engine(schema_url(postgres_url, schema_name), future=True)
        for _ in range(2)
    ]
    stores = [
        PostgresMemoryStore(
            engine,
            database_path=tmp_path / f"memory-{index}.db",
            artifactctl=None,
        )
        for index, engine in enumerate(engines)
    ]
    try:
        stores[0].put(_record("feedback-race"))
        original = postgres_write._feedback_update_values
        first_read = threading.Event()
        calls_lock = threading.Lock()
        calls = 0

        def delayed_feedback(*args, **kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
                is_first = calls == 1
            if is_first:
                first_read.set()
                time.sleep(0.2)
            return original(*args, **kwargs)

        monkeypatch.setattr(postgres_write, "_feedback_update_values", delayed_feedback)
        results: list[int] = []

        def apply(store: PostgresMemoryStore) -> None:
            results.append(
                store.apply_outcome_feedback(
                    ["feedback-race"],
                    outcome="success",
                    command_id="same-command",
                    observed_at=_now(),
                    feedback_delta=0.2,
                )
            )

        first = threading.Thread(target=apply, args=(stores[0],))
        second = threading.Thread(target=apply, args=(stores[1],))
        first.start()
        assert first_read.wait(timeout=2)
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive()
        assert not second.is_alive()
        assert sorted(results) == [0, 1]
        stored = stores[0].get("feedback-race")
        assert stored is not None
        assert stored.meta["outcome_feedback_command_ids"] == ["same-command"]
        assert stored.meta["outcome_success_count"] == 1
    finally:
        for engine in engines:
            engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
