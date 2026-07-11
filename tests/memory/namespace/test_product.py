from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.memory.models import MemoryNamespace, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _record(
    record_id: str,
    *,
    scope: str,
    namespace: MemoryNamespace | None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        title="Shared convention",
        content="shared deployment convention",
        namespace=namespace,
        created_at=f"2026-07-10T00:00:0{len(record_id)}Z",
        updated_at=f"2026-07-10T00:00:0{len(record_id)}Z",
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        return InMemoryMemoryStore()
    return SQLiteMemoryStore(tmp_path / "namespace.db", artifactctl=None)


@pytest.fixture
def seeded_store(store):
    full_namespace = MemoryNamespace(
        tenant_id="tenant-a",
        org_id="org-a",
        user_id="user-a",
        agent_id="agent-a",
        session_id="session-a",
        conversation_id="conversation-a",
        project_id="project-a",
        graph_id="graph-a",
    )
    store.put(_record("typed-a", scope="agent:agent-a", namespace=full_namespace))
    store.put(
        _record(
            "typed-b",
            scope="agent:agent-b",
            namespace=MemoryNamespace(
                user_id="user-b",
                agent_id="agent-b",
                project_id="project-b",
            ),
        )
    )
    store.put(_record("legacy", scope="session:legacy", namespace=None))
    return store, full_namespace


def test_partial_filter_is_conjunctive_and_store_isolated(seeded_store) -> None:
    store, _ = seeded_store

    matching = store.list(
        ListQueryOptions(
            scopes=[],
            namespaces=[MemoryNamespace(user_id="user-a", agent_id="agent-a")],
        )
    )
    wrong_user = store.list(
        ListQueryOptions(
            scopes=[],
            namespaces=[MemoryNamespace(user_id="user-b", agent_id="agent-a")],
        )
    )
    wrong_project = store.list(
        ListQueryOptions(
            scopes=[],
            namespaces=[MemoryNamespace(project_id="project-b", agent_id="agent-a")],
        )
    )

    assert [record.id for record in matching] == ["typed-a"]
    assert wrong_user == []
    assert wrong_project == []


def test_namespace_search_does_not_bleed_same_content(seeded_store) -> None:
    store, _ = seeded_store

    results = store.search(
        SearchQueryOptions(
            query="deployment",
            scopes=[],
            namespaces=[MemoryNamespace(user_id="user-a")],
        )
    )

    assert [record.id for record in results] == ["typed-a"]


def test_all_dimensions_round_trip(seeded_store) -> None:
    store, expected = seeded_store

    record = store.get("typed-a")

    assert record is not None
    assert record.namespace == expected
    assert record.namespace.as_dict() == expected.as_dict()


def test_legacy_scope_record_matches_canonical_bridge(seeded_store) -> None:
    store, _ = seeded_store

    results = store.list(
        ListQueryOptions(
            scopes=["session:legacy"],
            namespaces=[MemoryNamespace.from_scope("session:legacy")],
        )
    )

    assert [record.id for record in results] == ["legacy"]
    assert results[0].namespace is None
    assert results[0].effective_namespace.session_id == "legacy"


class _NamespaceDroppingStore:
    def __init__(self) -> None:
        self.record = _record("broad", scope="agent:agent-a", namespace=None)

    def list(self, options):
        return [self.record]

    def search(self, options):
        return [self.record]


def test_service_fails_closed_when_backend_drops_explicit_dimensions() -> None:
    service = MemoryService(_NamespaceDroppingStore())
    namespace = MemoryNamespace(user_id="user-a", agent_id="agent-a")

    listed = service.list(ListQueryOptions(scopes=[], namespaces=[namespace]))
    searched = service.search(
        SearchQueryOptions(
            query="deployment",
            scopes=[],
            namespaces=[namespace],
        )
    )

    assert listed == []
    assert searched == []
