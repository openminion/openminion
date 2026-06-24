from __future__ import annotations

from pathlib import Path

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.location import plugin as location_plugin


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"agent_id": "agent-location"},
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
        confirm=True,
    )


def test_location_set_default_maps_storage_unconfigured(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.identity_path = None
    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: None
    )

    payload = location_plugin._h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DEPENDENCY_MISSING"
    assert payload["data"]["reason_code"] == "storage_unconfigured"


def test_location_set_default_maps_storage_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.identity_path = tmp_path / "identity.db"
    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: None
    )

    payload = location_plugin._h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DEPENDENCY_MISSING"
    assert payload["data"]["reason_code"] == "storage_unavailable"


def test_location_set_default_maps_record_not_found(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)

    class _MissingRepo:
        def get_profile(self, _agent_id: str):
            return None

        def upsert_profile(self, _profile):
            return "sha256:missing"

    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: _MissingRepo()
    )
    payload = location_plugin._h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["data"]["reason_code"] == "record_not_found"


def test_location_set_default_maps_storage_exec_error(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)

    class _BrokenRepo:
        def get_profile(self, _agent_id: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: _BrokenRepo()
    )
    payload = location_plugin._h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EXEC_ERROR"
    assert payload["data"]["reason_code"] == "storage_exec_error"
