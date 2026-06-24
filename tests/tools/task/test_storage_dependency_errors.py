from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.task import plugin as task_plugin


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"agent_id": "agent-task"},
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "tools": {"allow_prefix": [""]},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def test_task_list_maps_storage_unconfigured(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.cron_db_path = None
    monkeypatch.setattr(task_plugin, "resolve_cron_repository", lambda _ctx: None)

    with pytest.raises(ToolRuntimeError) as exc:
        task_plugin._h_task_list({}, ctx)
    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unconfigured"


def test_task_list_maps_storage_unavailable(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.cron_db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(task_plugin, "resolve_cron_repository", lambda _ctx: None)

    with pytest.raises(ToolRuntimeError) as exc:
        task_plugin._h_task_list({}, ctx)
    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unavailable"


def test_task_cancel_maps_record_not_found(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    class _EmptyRepo:
        def get_cron_job(self, _job_id: str):
            return None

        def list_cron_jobs(self, *, limit: int = 50):
            del limit
            return []

    monkeypatch.setattr(
        task_plugin, "resolve_cron_repository", lambda _ctx: _EmptyRepo()
    )
    with pytest.raises(ToolRuntimeError) as exc:
        task_plugin._h_task_cancel({"task_id": "missing-task"}, ctx)
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.details.get("reason_code") == "record_not_found"


def test_task_list_maps_storage_exec_error(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    class _BrokenRepo:
        def list_cron_jobs(self, *, limit: int = 50):
            del limit
            raise RuntimeError("boom")

    monkeypatch.setattr(
        task_plugin, "resolve_cron_repository", lambda _ctx: _BrokenRepo()
    )
    with pytest.raises(ToolRuntimeError) as exc:
        task_plugin._h_task_list({}, ctx)
    assert exc.value.code == "EXEC_ERROR"
    assert exc.value.details.get("reason_code") == "storage_exec_error"
