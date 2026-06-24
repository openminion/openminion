from __future__ import annotations

from sophiagraph import (
    KnowledgeExplorerRequest,
    MemoryNamespace,
    MemoryRecord,
    SophiaGraphMemoryStore,
    StructuralLink,
    explore_knowledge,
)


def _ns() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="openminion", agent_id="agent", graph_id="main")


def test_openminion_can_consume_sophiagraph_explorer_packet_directly() -> None:
    store = SophiaGraphMemoryStore()
    namespace = _ns()
    store.put_record(
        MemoryRecord(
            id="decision",
            scope="agent:agent",
            type="fact",
            key="decision",
            title="Authentication Decision",
            content={"text": "Use JWT for authentication."},
            tags=["security"],
            created_at="2026-05-31T00:00:00+00:00",
            updated_at="2026-05-31T00:00:00+00:00",
            source="validated",
            confidence=0.9,
            namespace=namespace,
        )
    )
    store.put_record(
        MemoryRecord(
            id="implementation",
            scope="agent:agent",
            type="fact",
            key="implementation",
            title="Implementation Plan",
            content={"text": "Implement the Authentication Decision in gateway."},
            tags=["security"],
            created_at="2026-05-31T00:01:00+00:00",
            updated_at="2026-05-31T00:01:00+00:00",
            source="validated",
            confidence=0.9,
            namespace=namespace,
        )
    )
    store.put_link(
        StructuralLink(
            link_id="link-implementation-decision",
            source_record_id="implementation",
            target_record_id="decision",
            raw_target="Authentication Decision",
            link_kind="wikilink",
            resolution_status="resolved",
            namespace=namespace,
            relation_type="supports",
            context_before="Depends on ",
            context_after=" before coding.",
            created_at="2026-05-31T00:02:00+00:00",
        )
    )

    packet = explore_knowledge(
        store,
        KnowledgeExplorerRequest(
            scopes=["agent:agent"],
            namespaces=[namespace],
            query="JWT",
            root_record_id="decision",
        ),
    )

    assert [hit.record_id for hit in packet.hits] == ["decision"]
    assert [link.link_id for link in packet.backlinks] == [
        "link-implementation-decision"
    ]
    assert packet.graph is not None
    assert {node.record_id for node in packet.graph.nodes} == {
        "decision",
        "implementation",
    }
    assert packet.query_plan is not None
    assert not any(
        "openminion" in str(stage.details) for stage in packet.query_plan.stages
    )
