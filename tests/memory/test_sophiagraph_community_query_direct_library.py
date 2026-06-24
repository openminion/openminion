from __future__ import annotations

import inspect
import sys

from sophiagraph import (
    CommunityDetectionOptions,
    CommunityQueryOptions,
    GraphPatternNodePredicate,
    GraphPatternQuery,
    MemoryNamespace,
    MemoryRecord,
    MemoryRelation,
    SophiaGraphMemoryStore,
    StructuralLink,
    StructuralGraphQueryRequest,
    execute_graph_pattern_query,
    execute_structural_graph_query,
    query_communities,
)


def _ns() -> MemoryNamespace:
    return MemoryNamespace(
        tenant_id="openminion",
        agent_id="agent",
        graph_id="main",
    )


def _record(record_id: str, title: str) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:agent",
        type="fact",
        key=record_id,
        title=title,
        content={"text": title},
        tags=["task"],
        created_at="2026-06-02T00:00:00+00:00",
        updated_at="2026-06-02T00:00:00+00:00",
        source="validated",
        namespace=_ns(),
        meta={"properties": {"kind": "task"}},
    )


def test_openminion_can_consume_sophiagraph_community_packets_directly() -> None:
    store = SophiaGraphMemoryStore()
    for record in [
        _record("task-plan", "Plan tracker execution"),
        _record("task-verify", "Verify tracker execution"),
        _record("task-close", "Close tracker evidence"),
    ]:
        store.put_record(record)
    store.put_relation(
        MemoryRelation(
            relation_id="rel-plan-verify",
            source_record_id="task-plan",
            target_record_id="task-verify",
            relation_type="supports",
            created_at="2026-06-02T00:01:00+00:00",
        )
    )
    store.put_link(
        StructuralLink(
            link_id="link-plan-verify",
            source_record_id="task-plan",
            target_record_id="task-verify",
            raw_target="task-verify",
            link_kind="wikilink",
            resolution_status="resolved",
            relation_type="supports",
            namespace=_ns(),
            created_at="2026-06-02T00:01:00+00:00",
        )
    )
    store.put_link(
        StructuralLink(
            link_id="link-verify-close",
            source_record_id="task-verify",
            target_record_id="task-close",
            raw_target="task-close",
            link_kind="wikilink",
            resolution_status="resolved",
            relation_type="supports",
            namespace=_ns(),
            created_at="2026-06-02T00:02:00+00:00",
        )
    )
    store.put_relation(
        MemoryRelation(
            relation_id="rel-verify-close",
            source_record_id="task-verify",
            target_record_id="task-close",
            relation_type="supports",
            created_at="2026-06-02T00:02:00+00:00",
        )
    )

    packet = query_communities(
        store,
        CommunityQueryOptions(
            detection=CommunityDetectionOptions(
                scopes=["agent:agent"],
                namespaces=[_ns()],
                relation_types=["supports"],
            ),
            include_summary_refs=True,
            summary_reference_ids=["caller-authored-summary"],
        ),
    )
    pattern = execute_graph_pattern_query(
        store,
        GraphPatternQuery(
            query_id="openminion-task-pattern",
            scopes=["agent:agent"],
            namespaces=[_ns()],
            seed_record_ids=["task-plan"],
            node_predicates=[GraphPatternNodePredicate("kind", "eq", "task")],
            relation_types=["supports"],
            max_hops=2,
        ),
    )

    assert packet.summary_reference_ids == ["caller-authored-summary"]
    assert [community.record_ids for community in packet.communities] == [
        ["task-close", "task-plan", "task-verify"]
    ]
    assert [match.record_ids for match in pattern.matches] == [
        ["task-plan", "task-verify"],
        ["task-plan", "task-verify", "task-close"],
    ]


def test_openminion_can_consume_structural_graph_query_surface_directly() -> None:
    store = SophiaGraphMemoryStore()
    for record in [
        _record("task-plan", "Plan tracker execution"),
        _record("task-verify", "Verify tracker execution"),
        _record("task-close", "Close tracker evidence"),
    ]:
        store.put_record(record)
    for relation_id, source_id, target_id in [
        ("rel-plan-verify", "task-plan", "task-verify"),
        ("rel-verify-close", "task-verify", "task-close"),
    ]:
        store.put_relation(
            MemoryRelation(
                relation_id=relation_id,
                source_record_id=source_id,
                target_record_id=target_id,
                relation_type="supports",
                created_at="2026-06-02T00:01:00+00:00",
            )
        )
    for link_id, source_id, target_id in [
        ("link-plan-verify", "task-plan", "task-verify"),
        ("link-verify-close", "task-verify", "task-close"),
    ]:
        store.put_link(
            StructuralLink(
                link_id=link_id,
                source_record_id=source_id,
                target_record_id=target_id,
                raw_target=target_id,
                link_kind="wikilink",
                resolution_status="resolved",
                relation_type="supports",
                namespace=_ns(),
                created_at="2026-06-02T00:01:00+00:00",
            )
        )

    result = execute_structural_graph_query(
        store,
        StructuralGraphQueryRequest(
            query_id="openminion-structural-pattern",
            mode="pattern",
            scopes=["agent:agent"],
            namespaces=[_ns()],
            seed_record_ids=["task-plan"],
            node_predicates=[GraphPatternNodePredicate("kind", "eq", "task")],
            relation_types=["supports"],
            max_hops=2,
            limit=5,
        ),
    )

    assert [row.node_ids for row in result.rows] == [
        ["task-plan", "task-verify"],
        ["task-plan", "task-verify", "task-close"],
    ]
    assert [stage.stage for stage in result.planner] == [
        "mode",
        "seed_filter",
        "pattern_execute",
    ]


def test_community_query_fixture_imports_only_public_sophiagraph_paths() -> None:
    source = inspect.getsource(sys.modules[__name__])
    leaked = {
        "sophiagraph.query.community",
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
    }.intersection(source)
    assert not leaked, (
        f"test file reaches into non-public sophiagraph paths: {sorted(leaked)}"
    )
