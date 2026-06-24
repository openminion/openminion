from __future__ import annotations

import pytest

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.errors import MemctlError
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _record(
    record_id: str, *, scope: str = "session:s1", title: str = "record"
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        title=title,
        content={"text": title},
        created_at="2026-04-30T00:00:00+00:00",
        updated_at="2026-04-30T00:00:00+00:00",
    )


@pytest.fixture(params=["sqlite", "mock"], ids=["sqlite", "mock"])
def service(request: pytest.FixtureRequest, tmp_path) -> MemoryService:
    if request.param == "sqlite":
        store = SQLiteMemoryStore(tmp_path / "memory.db")
    else:
        store = InMemoryMemoryStore()
    svc = MemoryService(store=store)
    svc._store.put(_record("r1", title="alpha"))  # noqa: SLF001
    svc._store.put(_record("r2", title="beta"))  # noqa: SLF001
    svc._store.put(_record("r3", title="gamma", scope="session:s2"))  # noqa: SLF001
    return svc


def test_put_and_list_relations_round_trip(service: MemoryService) -> None:
    relation_id = service.put_relation(
        source_record_id="r1",
        target_record_id="r2",
        relation_type="supports",
        meta={"reason": "same project"},
    )

    relations = service.list_relations(record_id="r1")
    assert len(relations) == 1
    assert relations[0].relation_id == relation_id
    assert relations[0].relation_type == "supports"
    assert relations[0].meta["reason"] == "same project"


def test_get_related_records_filters_by_scope_and_type(service: MemoryService) -> None:
    service.put_relation(
        source_record_id="r1",
        target_record_id="r2",
        relation_type="supports",
    )
    service.put_relation(
        source_record_id="r1",
        target_record_id="r3",
        relation_type="depends_on",
    )

    related = service.get_related_records(
        record_id="r1",
        scopes=["session:s1"],
        relation_types=["supports"],
    )
    assert [item.id for item in related] == ["r2"]


def test_search_with_relations_merges_one_hop_neighbors(service: MemoryService) -> None:
    service.put_relation(
        source_record_id="r1",
        target_record_id="r2",
        relation_type="related_to",
    )

    hits = service.search_with_relations(
        query="alpha",
        scopes=["session:s1"],
        relation_types=["related_to"],
        limit=5,
        related_limit=2,
    )
    assert [item.id for item in hits] == ["r1", "r2"]


def test_relation_write_missing_record_fails() -> None:
    service = MemoryService(store=InMemoryMemoryStore())
    service._store.put(_record("r1"))  # noqa: SLF001

    with pytest.raises(MemctlError, match="record not found"):
        service.put_relation(
            source_record_id="r1",
            target_record_id="missing",
            relation_type="supports",
        )


def test_audited_store_records_relation_put_event() -> None:
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(InMemoryMemoryStore(), sink=sink)
    store.put(_record("r1"))
    store.put(_record("r2"))
    service = MemoryService(store=store)

    service.put_relation(
        source_record_id="r1",
        target_record_id="r2",
        relation_type="corrects",
    )

    assert any(event.event_type == "memory.relation.put" for event in sink.events)
