from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    ActiveEmbeddingModelSet,
    MemoryEmbedding,
    MemoryNamespace,
    MemoryRecord,
    SophiaGraphMemoryStore,
    VectorSpaceModelDescriptor,
    build_reembed_plan,
    detect_stale_embeddings,
    list_orphan_external_vector_ids,
)


def _ns() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="tenant", agent_id="openminion", graph_id="main")


def _record(updated_at: str) -> MemoryRecord:
    return MemoryRecord(
        id="rec-1",
        scope="agent:openminion",
        type="fact",
        key="rec-1",
        title="Fixture Record",
        content={"text": "fixture"},
        created_at="2026-06-05T00:00:00+00:00",
        updated_at=updated_at,
        namespace=_ns(),
        meta={},
    )


def _embedding(
    *,
    model: str,
    dimension: int = 4,
    updated_at: str = "2026-06-05T00:00:00+00:00",
    external_vector_id: str | None = None,
) -> MemoryEmbedding:
    return MemoryEmbedding(
        record_id="rec-1",
        vector_space="semantic",
        dimension=dimension,
        provider="provider-a",
        model=model,
        namespace=_ns(),
        created_at="2026-06-05T00:00:00+00:00",
        updated_at=updated_at,
        vector=[0.1, 0.2, 0.3, 0.4][:dimension],
        external_vector_id=external_vector_id,
        metadata={"fixture": True},
    )


def _active_set() -> ActiveEmbeddingModelSet:
    return ActiveEmbeddingModelSet(
        namespace=_ns(),
        vector_space="semantic",
        active_models=(
            VectorSpaceModelDescriptor(
                provider="provider-a",
                model="model-v2",
                dimension=4,
            ),
        ),
        updated_at="2026-06-06T00:00:00+00:00",
    )


def test_openminion_can_run_public_embedding_lifecycle_flow() -> None:
    store = SophiaGraphMemoryStore()
    store.put_record(_record(updated_at="2026-06-06T00:00:00+00:00"))
    store.put_embedding(_embedding(model="model-v1", external_vector_id="vec-1"))
    store.put_active_model_set(_active_set())

    findings = detect_stale_embeddings(
        store,
        namespace=_ns(),
        vector_space="semantic",
        active_models=_active_set(),
    )
    assert [finding.record_id for finding in findings] == ["rec-1"]

    plan = build_reembed_plan(
        store,
        namespace=_ns(),
        vector_space="semantic",
        target_model=_active_set().active_models[0],
        batch_size=1,
        active_models=_active_set(),
    )
    assert len(plan.batches) == 1

    for batch in plan.batches:
        for item in batch.items:
            store.put_embedding(
                MemoryEmbedding(
                    record_id=item.record_id,
                    vector_space=item.vector_space,
                    dimension=batch.target_model.dimension,
                    provider=batch.target_model.provider,
                    model=batch.target_model.model,
                    namespace=item.namespace,
                    created_at="2026-06-06T00:00:00+00:00",
                    updated_at="2026-06-06T01:00:00+00:00",
                    vector=[0.2, 0.3, 0.4, 0.5],
                    external_vector_id="vec-1",
                    metadata={"fixture": True},
                )
            )

    assert (
        detect_stale_embeddings(
            store,
            namespace=_ns(),
            vector_space="semantic",
            active_models=_active_set(),
        )
        == []
    )

    store.tombstone_record(
        "rec-1",
        deleted_at="2026-06-06T02:00:00+00:00",
        reason="fixture cleanup",
    )
    assert store.delete_embedding("rec-1", "semantic") is True
    assert list_orphan_external_vector_ids(store, namespace=_ns()) == [
        ("vec-1", "2026-06-06T01:00:00+00:00")
    ]


def test_embedding_lifecycle_fixture_uses_public_sophiagraph_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.embedding_lifecycle",
        "sophiagraph.models.embedding_lifecycle",
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    leaked = imports & forbidden
    assert not leaked, (
        f"fixture reaches into private SophiaGraph paths: {sorted(leaked)}"
    )
