from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.time import plugin as time_plugin


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"agent_id": "agent-time"},
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
        scope="READ_ONLY",
        confirm=False,
    )


def test_time_now_maps_storage_unconfigured(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.identity_path = None
    monkeypatch.setattr(time_plugin, "resolve_identity_repository", lambda _ctx: None)

    with pytest.raises(ToolRuntimeError) as exc:
        time_plugin._h_now({}, ctx)
    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unconfigured"


def test_time_now_maps_storage_unavailable(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.identity_path = tmp_path / "identity.db"
    monkeypatch.setattr(time_plugin, "resolve_identity_repository", lambda _ctx: None)

    with pytest.raises(ToolRuntimeError) as exc:
        time_plugin._h_now({}, ctx)
    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unavailable"


def test_time_now_maps_storage_exec_error(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    class _BrokenRepo:
        def get_profile(self, _agent_id: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        time_plugin, "resolve_identity_repository", lambda _ctx: _BrokenRepo()
    )

    with pytest.raises(ToolRuntimeError) as exc:
        time_plugin._h_now({}, ctx)
    assert exc.value.code == "EXEC_ERROR"
    assert exc.value.details.get("reason_code") == "storage_exec_error"
