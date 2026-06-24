from __future__ import annotations

from unittest.mock import MagicMock

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _make_service() -> tuple[InMemoryMemoryStore, MemoryService]:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return store, service


def _make_adapter(
    *, project_id: str | None = None
) -> tuple[InMemoryMemoryStore, MemoryService, MemoryServiceGatewayAdapter]:
    store, service = _make_service()
    return (
        store,
        service,
        MemoryServiceGatewayAdapter(
            service,
            agent_id="mii-phase1-agent",
            project_id=project_id,
        ),
    )


def test_search_semantic_returns_hits_from_all_requested_scopes() -> None:
    store, service = _make_service()
    for scope, record_id in (
        ("agent:mii-phase1-agent", "rec-agent"),
        ("project:proj-42", "rec-project"),
        ("global:system", "rec-global"),
    ):
        store.put(
            MemoryRecord(
                id=record_id,
                scope=scope,
                type="fact",
                title=f"{scope} weather",
                content="weather memory",
                created_at="2026-03-25T00:00:00+00:00",
                updated_at="2026-03-25T00:00:00+00:00",
            )
        )

    vector = MagicMock()

    def _search(*, query: str, top_k: int, filters: dict | None = None):  # type: ignore[no-untyped-def]
        scope = (filters or {}).get("scope")
        if scope == "agent:mii-phase1-agent":
            return [("rec-agent", 0.7, {})]
        if scope == "project:proj-42":
            return [("rec-project", 0.8, {})]
        if scope == "global:system":
            return [("rec-global", 0.9, {})]
        return []

    vector.search.side_effect = _search
    service.set_vector_adapter(vector)

    results = service.search_semantic(
        "weather",
        scopes=["agent:mii-phase1-agent", "project:proj-42", "global:system"],
        limit=10,
    )

    assert {record.id for record in results} == {
        "rec-agent",
        "rec-project",
        "rec-global",
    }


def test_capsule_prefers_relevant_long_term_records_for_non_empty_message() -> None:
    _store, service, adapter = _make_adapter()
    service.write_record(
        scope="agent:mii-phase1-agent",
        record_type="fact",
        title="Weather preference",
        content="weather toolkit note",
        tags=["weather"],
    )
    for index in range(25):
        service.write_record(
            scope="agent:mii-phase1-agent",
            record_type="fact",
            title=f"Recent fact {index}",
            content=f"recent unrelated note {index}",
            tags=["recent"],
        )

    relevant_context, _ = adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="weather",
    )
    fallback_context, _ = adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="",
    )

    assert "Weather preference" in relevant_context
    assert "Weather preference" not in fallback_context


def test_capsule_includes_project_and_global_scope_when_project_id_present() -> None:
    _store, service, adapter = _make_adapter(project_id="proj-42")
    service.write_record(
        scope="agent:mii-phase1-agent",
        record_type="fact",
        title="Agent note",
        content="agent continuity fact",
        tags=["agent"],
    )
    service.write_record(
        scope="project:proj-42",
        record_type="fact",
        title="Project note",
        content="project handbook",
        tags=["project"],
    )
    service.write_record(
        scope="global:system",
        record_type="pin",
        title="System note",
        content="global operating policy",
        tags=["global"],
    )

    context, _ = adapter.build_context_with_metadata(
        session_id="session-2",
        user_message="",
    )

    assert "Project note" in context
    assert "System note" in context


def test_retrieval_pipeline_keeps_project_scope_when_injected() -> None:
    _store, service = _make_service()
    retrieve_ctl = MagicMock(name="retrieve_ctl")
    retrieve_ctl.retrieve.return_value = []
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="mii-phase1-agent",
        project_id="proj-42",
        retrieve_ctl=retrieve_ctl,
    )

    adapter.build_retrieval_context_with_metadata(
        session_id="session-3",
        user_message="query",
    )

    first_filters = retrieve_ctl.retrieve.call_args_list[0].kwargs["filters"]
    assert first_filters["scope_keys"] == [
        "session:session-3",
        "agent:mii-phase1-agent",
        "project:proj-42",
    ]
