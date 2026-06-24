from __future__ import annotations

from datetime import datetime, timezone

from openminion.modules.tool import cli
from openminion.modules.tool.contracts.schemas import ResultEnvelope, WorkspaceInfo


def test_cli_has_app_entrypoint():

    assert hasattr(cli, "app"), "cli module must expose Typer app"


def test_result_envelope_schema(workspace_fixture):
    workspace_dir, _policy_path = workspace_fixture

    envelope = ResultEnvelope(
        ok=True,
        tool="file.list_dir",
        run_id="test-run",
        request_id="req-123",
        policy_scope="READ_ONLY",
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=10,
        workspace=WorkspaceInfo(root=str(workspace_dir), relative_root="."),
    )

    assert envelope.ok is True
    assert envelope.workspace.root == str(workspace_dir)
