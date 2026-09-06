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
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.postgres import write as postgres_write
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
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

    store.touch_last_hit(upserted.id)
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
