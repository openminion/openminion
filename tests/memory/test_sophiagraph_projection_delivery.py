from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    FakeGraphBackendAdapter,
    MemoryNamespace,
    MemoryRecord,
    SophiaGraphMemoryStore,
)
from sophiagraph.projections import (
    GraphChangeProjector,
    ProjectionTarget,
    get_projection_health,
    run_projection_batch,
)


def test_openminion_can_schedule_projection_through_public_contracts() -> None:
    store = SophiaGraphMemoryStore()
    namespace = MemoryNamespace(agent_id="openminion", graph_id="main")
    store.put_record(
        MemoryRecord(
            id="record-1",
            scope="agent:openminion",
            type="fact",
            key="record-1",
            title="Projection fixture",
            content={"text": "caller supplied"},
            created_at="2026-07-16T12:00:00+00:00",
            updated_at="2026-07-16T12:00:00+00:00",
            namespace=namespace,
        )
    )
    store.register_projection_target(
        ProjectionTarget(
            target_id="openminion-graph",
            kind="graph",
            adapter_name="fake",
            namespace=namespace,
        )
    )
    backend = FakeGraphBackendAdapter()
    result = run_projection_batch(
        store,
        target_id="openminion-graph",
        projector=GraphChangeProjector(backend),
        owner_id="openminion-explicit-scheduler",
        now="2026-07-16T12:00:01+00:00",
    )
    health = get_projection_health(
        store,
        target_id="openminion-graph",
        now="2026-07-16T12:00:01+00:00",
    )
    assert result.applied == 1
    assert health.lag == 0
    assert backend.inventory()[0].object_id == "record-1"


def test_projection_fixture_uses_public_sophiagraph_imports_only() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports == {"__future__", "pathlib", "sophiagraph", "sophiagraph.projections"}
