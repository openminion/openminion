from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    MemoryNamespace,
    WorkspaceFilePrimaryNoteOptions,
    initialize_workspace,
    scan_workspace_sync,
    workspace_file_primary_note_put,
    workspace_sync_status,
)


def _ns() -> MemoryNamespace:
    return MemoryNamespace(agent_id="openminion", graph_id="main")


def test_openminion_can_run_public_workspace_live_sync_flow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_root = tmp_path / "notes"
    initialize_workspace(
        workspace,
        scope="agent:openminion",
        namespace=_ns(),
        label="workspace-sync",
        vault_id="vault-sync",
    )

    result = workspace_file_primary_note_put(
        workspace,
        source_root,
        options=WorkspaceFilePrimaryNoteOptions(
            note_key="welcome",
            title="Welcome",
            body="Hello from OpenMinion.",
        ),
    )
    assert (source_root / result.relative_path).exists()

    plan = scan_workspace_sync(workspace, source_root)
    assert plan.deltas == ()

    status = workspace_sync_status(workspace, source_root, plan=plan)
    assert status.fresh_count == 1
    assert status.pending_delta_count == 0


def test_workspace_live_sync_fixture_uses_public_sophiagraph_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.workspace_sync",
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
