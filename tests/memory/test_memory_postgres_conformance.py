from __future__ import annotations

import datetime
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
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
    RecordOrder,
    SearchQueryOptions,
)
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.postgres import (
    candidate_supersession as postgres_candidate_supersession,
)
from openminion.modules.memory.storage.postgres import queries as postgres_queries
from openminion.modules.memory.storage.postgres import write as postgres_write
from openminion.modules.memory.storage.sqlite import search as sqlite_search
from openminion.modules.memory.storage.sqlite import store as sqlite_store
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


def _candidate(
    candidate_id: str,
    *,
    status: str = "proposed",
    observed_at: str | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="s1",
        proposed_scope="session:s1",
        type="fact",
        content={"text": f"candidate-{candidate_id}"},
        status=status,
        created_at=observed_at,
        updated_at=observed_at,
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


def test_candidate_oldest_updated_order_rotates_deferred_work(store) -> None:
    for candidate_id, observed_at in (
        ("candidate-a", "2026-09-29T00:00:00+00:00"),
        ("candidate-b", "2026-09-29T00:01:00+00:00"),
    ):
        store.candidate_put(_candidate(candidate_id, observed_at=observed_at))

    options = CandidateListOptions(
        status="proposed",
        limit=1,
        order_by=RecordOrder.UPDATED_AT_ASC,
    )
    assert [item.candidate_id for item in store.candidate_list(options)] == [
        "candidate-a"
    ]

    store.candidate_update(
        "candidate-a",
        {"updated_at": "2026-09-29T00:02:00+00:00"},
    )
    assert [item.candidate_id for item in store.candidate_list(options)] == [
        "candidate-b"
    ]


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


def _force_expiry(store, record_id: str, value: str) -> None:
    """Store a raw value, as a row a caller-supplied expiry can hold."""
    if isinstance(store, SQLiteMemoryStore):
        with store._connect() as conn:
            conn.execute(
                "UPDATE memory_records SET expires_at = ? WHERE id = ?",
                (value, record_id),
            )
        return
    with store._engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE memory_records SET expires_at = :value WHERE id = :id"),
            {"value": value, "id": record_id},
        )


def test_active_reads_compare_stored_expiry_as_an_instant(store) -> None:
    scope = "session:expiry-instant"
    future = store.upsert(
        scope,
        "fact",
        "expiry:future",
        {
            "content": {"text": "The staging cluster runs in Frankfurt."},
            "entities": ["frankfurt"],
        },
    )
    past = store.upsert(
        scope,
        "fact",
        "expiry:past",
        {"content": {"text": "The staging cluster ran in Bonn."}, "entities": ["bonn"]},
    )
    garbage = store.upsert(
        scope,
        "fact",
        "expiry:garbage",
        {
            "content": {"text": "The staging cluster runs in Mainz."},
            "entities": ["mainz"],
        },
    )
    impossible = store.upsert(
        scope,
        "fact",
        "expiry:impossible",
        {
            "content": {"text": "The staging cluster runs in Trier."},
            "entities": ["trier"],
        },
    )
    calendar = store.upsert(
        scope,
        "fact",
        "expiry:calendar",
        {
            "content": {"text": "The staging cluster runs in Kassel."},
            "entities": ["kassel"],
        },
    )
    year_zero = store.upsert(
        scope,
        "fact",
        "expiry:year-zero",
        {
            "content": {"text": "The staging cluster runs in Fulda."},
            "entities": ["fulda"],
        },
    )
    clock = store.upsert(
        scope,
        "fact",
        "expiry:clock",
        {
            "content": {"text": "The staging cluster runs in Wiesbaden."},
            "entities": ["wiesbaden"],
        },
    )
    _force_expiry(store, future.id, "2030-01-01T00:00:00.500000+14:00")
    _force_expiry(store, past.id, "2020-01-01T00:00:00.500000-12:00")
    # None of these is an instant: the guard rejects them before the cast, so the read
    # must not raise (PostgreSQL raises on an impossible date) and must not hide a row.
    _force_expiry(store, garbage.id, "!")
    _force_expiry(store, impossible.id, "2026-99-01T00:00:00+00:00")
    _force_expiry(store, calendar.id, "2020-02-30T00:00:00+00:00")
    _force_expiry(store, year_zero.id, "0000-01-01T00:00:00+00:00")
    _force_expiry(store, clock.id, "2020-01-01T24:59:00+00:00")

    listed = {item.id for item in store.list(ListQueryOptions(scopes=[scope]))}
    assert listed == {
        future.id,
        garbage.id,
        impossible.id,
        calendar.id,
        year_zero.id,
        clock.id,
    }

    for query, expected in (
        ("Frankfurt", {future.id}),
        ("Bonn", set()),
        ("Mainz", {garbage.id}),
        ("Trier", {impossible.id}),
        ("Kassel", {calendar.id}),
        ("Fulda", {year_zero.id}),
        ("Wiesbaden", {clock.id}),
    ):
        hits = store.search(SearchQueryOptions(query=query, scopes=[scope]))
        assert {hit.id for hit in hits} == expected

    assert [item.id for item in store.retrieve_by_entities(["frankfurt"], [scope])] == [
        future.id
    ]
    assert [item.id for item in store.retrieve_by_entities(["wiesbaden"], [scope])] == [
        clock.id
    ]
    assert store.retrieve_by_entities(["bonn"], [scope]) == []

    history = store.search(
        SearchQueryOptions(query="Bonn", scopes=[scope], include_invalidated=True)
    )
    assert {hit.id for hit in history} == {past.id}
    assert store.get(past.id) is not None


QUERY_INSTANT = datetime.datetime(
    2026, 11, 6, 16, 0, 0, 100000, tzinfo=datetime.timezone.utc
)


def _freeze_read_clock(monkeypatch: pytest.MonkeyPatch, store, moment) -> None:
    """Pin the instant the read predicates compare stored expiry against."""

    class _FixedClock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is not None else moment.replace(tzinfo=None)

    clock = SimpleNamespace(
        datetime=_FixedClock, timezone=datetime.timezone, timedelta=datetime.timedelta
    )
    if isinstance(store, SQLiteMemoryStore):
        monkeypatch.setattr(sqlite_search, "datetime", clock)
        monkeypatch.setattr(sqlite_store, "datetime", clock)
        return
    monkeypatch.setattr(postgres_queries, "datetime", clock)


def test_stored_expiry_boundaries_compare_as_instants(store, monkeypatch) -> None:
    """Equivalent spellings agree, one microsecond survives, and 0001 is the floor."""
    scope = "session:expiry-boundary"
    values = {
        "six": "2026-11-06T16:00:00.100000+00:00",
        "one": "2026-11-06T16:00:00.1+00:00",
        "plus-two": "2026-11-06T18:00:00.1+02:00",
        "later": "2026-11-06T16:00:00.100001+00:00",
        "crossing": "2026-11-07T01:00:00.100000+14:00",
        "crossing-utc": "2026-11-06T11:00:00.100000+00:00",
        "whole": "2026-11-06T16:00:00+00:00",
    }
    ids = {}
    for key, value in values.items():
        record = store.upsert(
            scope,
            "fact",
            f"expiry:{key}",
            {"content": {"text": f"Boundary row {key}."}, "entities": ["frankfurt"]},
        )
        _force_expiry(store, record.id, value)
        ids[key] = record.id

    def visible() -> set[str]:
        listed = {item.id for item in store.list(ListQueryOptions(scopes=[scope]))}
        by_entity = {
            item.id for item in store.retrieve_by_entities(["frankfurt"], [scope])
        }
        assert listed == by_entity
        return listed

    _freeze_read_clock(monkeypatch, store, QUERY_INSTANT)
    assert visible() == {ids["later"]}

    _freeze_read_clock(
        monkeypatch, store, QUERY_INSTANT - datetime.timedelta(microseconds=1)
    )
    assert visible() == {ids["six"], ids["one"], ids["plus-two"], ids["later"]}

    _freeze_read_clock(
        monkeypatch, store, QUERY_INSTANT + datetime.timedelta(seconds=1)
    )
    assert visible() == set()

    _freeze_read_clock(monkeypatch, store, QUERY_INSTANT.replace(hour=11))
    assert ids["crossing"] not in visible()
    assert ids["crossing-utc"] not in visible()
    _freeze_read_clock(
        monkeypatch,
        store,
        QUERY_INSTANT.replace(hour=11) - datetime.timedelta(microseconds=1),
    )
    assert {ids["crossing"], ids["crossing-utc"]} <= visible()

    # A value with no fraction is midnight-sharp, not the end of its second.
    whole_second = QUERY_INSTANT.replace(microsecond=0)
    _freeze_read_clock(monkeypatch, store, whole_second)
    assert ids["whole"] not in visible()
    _freeze_read_clock(
        monkeypatch, store, whole_second - datetime.timedelta(microseconds=1)
    )
    assert ids["whole"] in visible()


def test_an_expiry_written_by_the_store_keeps_the_caller_value(store) -> None:
    """Writes are not normalised: the read side has to understand what was stored."""
    written = "2030-01-01T00:00:00.500000+02:00"
    created = store.upsert(
        "session:expiry-canonical",
        "fact",
        "expiry:canonical",
        {"content": {"text": "Deadlines are stored as given."}, "expires_at": written},
    )

    stored = store.get(created.id)
    assert stored is not None
    assert stored.expires_at == written
