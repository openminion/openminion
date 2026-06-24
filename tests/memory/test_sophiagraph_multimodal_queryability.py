from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    ArtifactCitation,
    ArtifactProjectionSegment,
    ArtifactRecord,
    ArtifactTextProjection,
    ArtifactTextQueryOptions,
    MemoryNamespace,
    MemoryRecord,
    SophiaGraphMemoryStore,
    query_artifact_text,
)


def _ns() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="tenant", agent_id="openminion", graph_id="main")


def test_openminion_can_submit_artifact_projection_and_query_it() -> None:
    store = SophiaGraphMemoryStore()
    record = MemoryRecord(
        id="rec-derived",
        scope="agent:openminion",
        type="fact",
        key="rec-derived",
        title="Derived Text",
        content="diagram summary with payment schedule",
        created_at="2026-06-17T00:00:00+00:00",
        updated_at="2026-06-17T00:00:00+00:00",
        namespace=_ns(),
        meta={},
    )
    artifact = ArtifactRecord(
        artifact_id="art-diagram-1",
        uri="vault:images/diagram.png",
        sha256="f" * 64,
        mime="image/png",
        size_bytes=4096,
        namespace=_ns(),
        source_class="screenshot",
        source_owner="openminion",
        created_at="2026-06-17T00:00:00+00:00",
        derived_text_record_id=record.id,
        target_record_id="rec-root",
    )
    projection = ArtifactTextProjection(
        projection_id="proj-diagram-1",
        artifact_id=artifact.artifact_id,
        derived_text_record_id=record.id,
        namespace=_ns(),
        projection_kind="ocr_text",
        adapter_id="openminion:test",
        source_sha256=artifact.sha256,
        source_mime=artifact.mime,
        created_at="2026-06-17T00:00:00+00:00",
        segments=(
            ArtifactProjectionSegment(
                segment_id="seg-1",
                ordinal=0,
                text="diagram summary with payment schedule",
                citations=(
                    ArtifactCitation(
                        citation_id="cit-1",
                        artifact_id=artifact.artifact_id,
                        kind="segment",
                        segment_id="seg-1",
                    ),
                ),
            ),
        ),
    )
    store.put_record(record)
    store.put_artifact(artifact)
    store.put_artifact_projection(projection)

    result = query_artifact_text(
        store,
        ArtifactTextQueryOptions(query="payment"),
        source_owner="openminion",
    )

    assert len(result.hits) == 1
    assert result.hits[0].artifact_id == "art-diagram-1"
    assert result.hits[0].projection_kind == "ocr_text"
    assert result.hits[0].matched_segment_ids == ("seg-1",)


def test_multimodal_queryability_fixture_uses_public_sophiagraph_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.query.artifacts",
        "sophiagraph.models.artifact_projection",
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
