from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_pragmagraph_src() -> Path:
    override = os.environ.get("OPENMINION_PRAGMAGRAPH_SRC", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    workspace_root = Path(__file__).resolve().parents[4]
    return (workspace_root / "pragmagraph" / "src").resolve(strict=False)


def ensure_pragmagraph_src_on_path() -> Path:
    src_root = _resolve_pragmagraph_src()
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    return src_root


ensure_pragmagraph_src_on_path()

TEST_NAMESPACE = "fixture"
TEST_QUERY = "RuntimeGraph"
RUNTIME_NODE_ID = "pyclass:RuntimeGraph"
BOOTSTRAP_NODE_ID = "pysymbol:build_gateway_service"


def build_pragmagraph_snapshot(*, namespace: str = TEST_NAMESPACE):
    from pragmagraph.models import GraphEdge, GraphNode, GraphSnapshot, SourceRef

    return GraphSnapshot(
        namespace=namespace,
        root_path=".",
        nodes=(
            GraphNode(
                id=RUNTIME_NODE_ID,
                kind="python_class",
                label="RuntimeGraph",
                source_ref=SourceRef(path="src/app.py", line=3),
                text="RuntimeGraph coordinates knowledge graph provider results.",
                metadata={"module": "app", "symbol": "RuntimeGraph"},
            ),
            GraphNode(
                id=BOOTSTRAP_NODE_ID,
                kind="python_symbol",
                label="build_gateway_service",
                source_ref=SourceRef(
                    path="src/openminion/services/runtime/bootstrap.py", line=685
                ),
                text="build_gateway_service wires RuntimeGraph into the gateway.",
                metadata={
                    "module": "openminion.services.runtime.bootstrap",
                    "symbol": "build_gateway_service",
                },
            ),
        ),
        edges=(
            GraphEdge(
                id="edge:runtime-uses-bootstrap",
                kind="uses",
                source_id=RUNTIME_NODE_ID,
                target_id=BOOTSTRAP_NODE_ID,
                source_ref=SourceRef(path="src/app.py", line=8),
                metadata={"reason": "runtime delegates to bootstrap"},
            ),
        ),
        stats={"node_count": 2, "edge_count": 1},
    )


def write_pragmagraph_snapshot(path: Path, *, namespace: str = TEST_NAMESPACE):
    from pragmagraph.storage import save_snapshot

    snapshot = build_pragmagraph_snapshot(namespace=namespace)
    save_snapshot(snapshot, path)
    return snapshot


def write_graphify_payload(path: Path, *, namespace: str = TEST_NAMESPACE):
    from pragmagraph.graphify import to_graphify_payload

    snapshot = build_pragmagraph_snapshot(namespace=namespace)
    path.write_text(json.dumps(to_graphify_payload(snapshot)), encoding="utf-8")
    return snapshot
