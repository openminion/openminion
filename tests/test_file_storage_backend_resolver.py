from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.file.backends import InMemoryStorageBackend, LocalStorageBackend
from openminion.tools.file.plugin import (
    _get_backend,
    _h_edit_file,
    _h_search_files,
    _reset_backend_cache_for_tests,
    _resolve_path_lexical,
    _resolve_workspace_root,
)


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    _reset_backend_cache_for_tests()
    try:
        yield
    finally:
        _reset_backend_cache_for_tests()


def _ctx(
    tmp_path: Path,
    *,
    backend_type: str | None = None,
    run_root_name: str = "run",
) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root = tmp_path / run_root_name
    run_root.mkdir(parents=True, exist_ok=True)
    raw = {
        "workspace_root": str(workspace),
        "paths": {
            "read_allow": [str(workspace)],
            "write_allow": [str(workspace)],
            "deny": [],
        },
    }
    if backend_type is not None:
        raw["file_backend"] = backend_type
    policy = Policy(raw=raw)
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def test_get_backend_defaults_to_local_and_caches_same_context(tmp_path: Path):
    ctx = _ctx(tmp_path)

    first = _get_backend(ctx)
    second = _get_backend(ctx)

    assert isinstance(first, LocalStorageBackend)
    assert first is second


def test_get_backend_isolated_by_run_root(tmp_path: Path):
    first_ctx = _ctx(tmp_path, backend_type="memory", run_root_name="run-one")
    second_ctx = _ctx(tmp_path, backend_type="memory", run_root_name="run-two")

    first = _get_backend(first_ctx)
    second = _get_backend(second_ctx)

    assert isinstance(first, InMemoryStorageBackend)
    assert isinstance(second, InMemoryStorageBackend)
    assert first is not second


def test_get_backend_isolated_by_workspace_root_for_shared_run_root(tmp_path: Path):
    first_ctx = _ctx(tmp_path, backend_type="memory", run_root_name="shared-run")
    second_ctx = _ctx(tmp_path, backend_type="memory", run_root_name="shared-run")
    second_workspace = tmp_path / "second-workspace"
    second_workspace.mkdir()
    second_ctx.workspace = second_workspace
    second_ctx.policy.raw["workspace_root"] = str(second_workspace)
    second_ctx.policy.raw["paths"] = {
        "read_allow": [str(second_workspace)],
        "write_allow": [str(second_workspace)],
        "deny": [],
    }

    first = _get_backend(first_ctx)
    second = _get_backend(second_ctx)

    assert isinstance(first, InMemoryStorageBackend)
    assert isinstance(second, InMemoryStorageBackend)
    assert first is not second


def test_get_backend_memory_retains_state_within_session(tmp_path: Path):
    ctx = _ctx(tmp_path, backend_type="memory")

    first = _get_backend(ctx)
    first.write(str(ctx.workspace / "alpha.txt"), "alpha")
    second = _get_backend(ctx)

    assert first is second
    result = second.read(str(ctx.workspace / "alpha.txt"))
    assert result.content == "alpha"


def test_memory_backend_services_file_search_without_local_fs_leak(tmp_path: Path):
    ctx = _ctx(tmp_path, backend_type="memory")
    backend = _get_backend(ctx)
    host_only = ctx.workspace / "notes" / "host-only.txt"
    host_only.parent.mkdir(parents=True, exist_ok=True)
    host_only.write_text("host filesystem only", encoding="utf-8")
    backend.write(str(ctx.workspace / "notes" / "alpha.txt"), "urgent memory note")

    result = _h_search_files({"path": "notes", "query": "urgent"}, ctx)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["path"] == str(ctx.workspace / "notes" / "alpha.txt")
    assert not (ctx.workspace / "notes" / "alpha.txt").exists()

    host_result = _h_search_files({"path": "notes", "query": "filesystem"}, ctx)

    assert host_result["ok"] is True
    assert host_result["count"] == 0


def test_memory_backend_services_file_edit_without_local_fs_leak(tmp_path: Path):
    ctx = _ctx(tmp_path, backend_type="memory")
    backend = _get_backend(ctx)
    target = ctx.workspace / "notes" / "alpha.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("host file should remain unchanged", encoding="utf-8")
    backend.write(str(target), "alpha\nbeta\n")

    result = _h_edit_file(
        {
            "path": "notes/alpha.txt",
            "operations": [{"op": "replace", "old_text": "beta", "new_text": "gamma"}],
        },
        ctx,
    )

    assert result == {
        "ok": True,
        "path": str(target),
        "operations_applied": 1,
        "source": "file_module",
    }
    assert backend.read(str(target)).content == "alpha\ngamma\n"
    assert target.read_text(encoding="utf-8") == "host file should remain unchanged"


def test_get_backend_rejects_unknown_backend(tmp_path: Path):
    ctx = _ctx(tmp_path, backend_type="bogus")

    with pytest.raises(ToolRuntimeError) as excinfo:
        _get_backend(ctx)

    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert excinfo.value.message == "unknown file backend: bogus"


def test_resolve_path_lexical_passes_operation_to_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ctx = _ctx(tmp_path)
    calls: list[tuple[str, Path, str]] = []

    def _record(raw_path: str, workspace: Path, operation: str) -> Path:
        calls.append((raw_path, workspace, operation))
        return Path(raw_path)

    monkeypatch.setattr(ctx.policy, "ensure_path_allowed", _record)

    resolved = _resolve_path_lexical(ctx, "nested/alpha.txt", operation="write")

    assert resolved == str(ctx.workspace / "nested" / "alpha.txt")
    assert calls == [
        (
            str(ctx.workspace / "nested" / "alpha.txt"),
            ctx.workspace,
            "write",
        )
    ]


def test_resolve_path_lexical_rejects_workspace_escape(tmp_path: Path):
    ctx = _ctx(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        _resolve_path_lexical(ctx, "../outside.txt", operation="read")

    assert excinfo.value.code == "POLICY_DENIED"
    assert "path escapes workspace root: ../outside.txt" in excinfo.value.message
    assert "Use a relative path under the workspace root" in excinfo.value.message
    assert excinfo.value.details["retry_path"] == "tmp/outside.txt"


def test_resolve_path_lexical_suggests_workspace_local_tmp_for_absolute_tmp(
    tmp_path: Path,
):
    ctx = _ctx(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        _resolve_path_lexical(ctx, "/tmp/http_server.asm", operation="write")

    assert excinfo.value.code == "POLICY_DENIED"
    assert excinfo.value.details["retry_path"] == "tmp/http_server.asm"
    assert "tmp/http_server.asm" in excinfo.value.message


def test_resolve_path_lexical_uses_context_metadata_cwd_for_relative_paths(
    tmp_path: Path,
):
    ctx = _ctx(tmp_path)
    nested = ctx.workspace / "openminion"
    nested.mkdir()
    ctx.policy.raw["context_metadata"] = {"cwd": str(nested)}

    resolved = _resolve_path_lexical(ctx, "target.cpp", operation="write")

    assert resolved == str(nested / "target.cpp")


def test_resolve_path_lexical_strips_duplicate_workspace_basename_prefix(
    tmp_path: Path,
):
    ctx = _ctx(tmp_path)

    resolved = _resolve_path_lexical(
        ctx,
        "workspace/pyproject.toml",
        operation="read",
    )

    assert resolved == str(ctx.workspace / "pyproject.toml")


def test_resolve_path_lexical_honors_workspace_root_env_when_policy_has_no_root(
    tmp_path: Path,
):
    ctx = _ctx(tmp_path)
    scratch = ctx.workspace / "scratch-project"
    scratch.mkdir()
    ctx.policy.raw.pop("workspace_root")
    ctx.env = {"OPENMINION_WORKSPACE_ROOT": str(scratch)}

    resolved = _resolve_path_lexical(
        ctx,
        "scratch-project/pyproject.toml",
        operation="read",
    )

    assert resolved == str(scratch / "pyproject.toml")


def test_resolve_workspace_root_prefers_policy_root_over_tool_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ctx = _ctx(tmp_path)
    broad_workspace = tmp_path / "parent-workspace"
    broad_workspace.mkdir()

    monkeypatch.setattr(
        "openminion.tools.file.plugin.resolve_tool_workspace_root",
        lambda *, env, fallback: broad_workspace,
    )

    assert _resolve_workspace_root(ctx) == ctx.workspace


def test_resolve_path_lexical_workspace_root_env_overrides_context_workspace(
    tmp_path: Path,
):
    ctx = _ctx(tmp_path)
    scratch = ctx.workspace / "scratch-project"
    scratch.mkdir()
    ctx.env = {"OPENMINION_WORKSPACE_ROOT": str(scratch)}

    resolved = _resolve_path_lexical(
        ctx,
        "scratch-project/pyproject.toml",
        operation="read",
    )

    assert resolved == str(scratch / "pyproject.toml")
