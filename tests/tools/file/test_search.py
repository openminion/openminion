from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from openminion.modules.tool.contracts.model_ids import MODEL_FILE_SEARCH
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.file.plugin import (
    _h_search_files,
    _resolve_workspace_root,
    register,
    _reset_backend_cache_for_tests,
)


class _AllowPolicy:
    raw: dict = {}

    def ensure_path_allowed(self, path, *, workspace, operation):
        return Path(path).resolve()

    def limit_int(self, key, default):
        return default


class _DenyEscapePolicy(_AllowPolicy):
    def __init__(self, workspace: Path):
        self._workspace = workspace
        self.raw = {}

    def ensure_path_allowed(self, path, *, workspace, operation):
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            raise ToolRuntimeError("POLICY_DENIED", f"path escapes workspace: {path}")
        return resolved


class _DenySubtreePolicy(_AllowPolicy):
    def __init__(self, denied_root: Path):
        self._denied_root = denied_root.resolve()
        self.raw = {}

    def ensure_path_allowed(self, path, *, workspace, operation):
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(self._denied_root)
        except ValueError:
            return resolved
        raise ToolRuntimeError(
            "POLICY_DENIED", f"path denied by subtree policy: {path}"
        )


class _FakeCtx:
    def __init__(self, workspace: Path, policy=None):
        self.workspace = workspace
        self.run_root = workspace
        self.scope = "READ_ONLY"
        self.confirm = False
        self.env = {}
        self.policy = policy or _AllowPolicy()
        self.policy.raw = {"workspace_root": str(workspace)}


@pytest.fixture()
def workspace():
    _reset_backend_cache_for_tests()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "hello.txt").write_text("hello world\nsecond line\nthird line")
        (p / "other.py").write_text("def foo():\n    return 42\n")
        (p / "binary.bin").write_bytes(b"\x00\x01\x02binary data")
        sub = p / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested content here\nmore nested")
        yield p


def test_file_search_literal_match(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files({"path": str(workspace), "query": "hello"}, ctx)
    assert result["ok"] is True
    assert result["count"] >= 1
    paths = [m["path"] for m in result["matches"]]
    assert any("hello.txt" in p for p in paths)


def test_resolve_workspace_root_handles_envless_context(workspace):
    ctx = _FakeCtx(workspace)
    delattr(ctx, "env")

    assert _resolve_workspace_root(ctx) == workspace.resolve(strict=False)


def test_resolve_workspace_root_prefers_explicit_env_even_when_it_matches_cwd(
    workspace, monkeypatch
):
    ctx = _FakeCtx(Path("/tmp/fallback-workspace"))
    ctx.env = {"OPENMINION_WORKSPACE_ROOT": str(workspace)}
    monkeypatch.chdir(workspace)

    assert _resolve_workspace_root(ctx) == workspace.resolve(strict=False)


def test_file_search_no_match(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files({"path": str(workspace), "query": "zzznomatch"}, ctx)
    assert result["ok"] is True
    assert result["count"] == 0


def test_file_search_regex_mode(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files(
        {"path": str(workspace), "query": r"def \w+", "regex": True}, ctx
    )
    assert result["ok"] is True
    assert result["count"] >= 1
    assert any("other.py" in m["path"] for m in result["matches"])


def test_file_search_regex_invalid(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files(
        {"path": str(workspace), "query": "[invalid", "regex": True}, ctx
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_file_search_context_lines_shape(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files(
        {
            "path": str(workspace),
            "query": "second line",
            "context_lines": 1,
        },
        ctx,
    )
    assert result["ok"] is True
    match = next(match for match in result["matches"] if match["line"] == 2)
    assert match["snippet"] == "hello world\nsecond line\nthird line"


def test_file_search_binary_file_skipped(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files({"path": str(workspace), "query": "binary"}, ctx)
    assert result["ok"] is True
    # binary.bin should be skipped (contains null bytes)
    for match in result["matches"]:
        assert "binary.bin" not in match["path"]


def test_file_search_hidden_files_excluded_by_default(workspace):
    hidden_dir = workspace / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("very secret hello")
    ctx = _FakeCtx(workspace)

    result = _h_search_files({"path": str(workspace), "query": "secret"}, ctx)

    assert result["ok"] is True
    assert result["count"] == 0


def test_file_search_include_hidden_opt_in(workspace):
    hidden_dir = workspace / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("very secret hello")
    ctx = _FakeCtx(workspace)

    result = _h_search_files(
        {
            "path": str(workspace),
            "query": "secret",
            "include_hidden": True,
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["path"].endswith("secret.txt")


def test_file_search_max_matches_respected(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files(
        {"path": str(workspace), "query": ".", "regex": True, "max_matches": 2}, ctx
    )
    assert result["ok"] is True
    assert len(result["matches"]) <= 2


def test_file_search_nested_files(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files({"path": str(workspace), "query": "nested"}, ctx)
    assert result["ok"] is True
    assert any("nested.txt" in m["path"] for m in result["matches"])


def test_file_search_path_not_found(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files(
        {"path": str(workspace / "nonexistent"), "query": "x"}, ctx
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_file_search_workspace_boundary_enforced(workspace):
    policy = _DenyEscapePolicy(workspace)
    ctx = _FakeCtx(workspace, policy=policy)
    ctx.policy.raw = {"workspace_root": str(workspace)}
    result = _h_search_files({"path": "/tmp", "query": "x"}, ctx)
    assert result["ok"] is False
    assert result["error"]["code"] == "POLICY_DENIED"


def test_file_search_case_insensitive_default(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files({"path": str(workspace), "query": "HELLO"}, ctx)
    assert result["ok"] is True
    assert any("hello.txt" in m["path"] for m in result["matches"])


def test_file_search_case_sensitive(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_search_files(
        {"path": str(workspace), "query": "HELLO", "case_sensitive": True}, ctx
    )
    assert result["ok"] is True
    assert result["count"] == 0


def test_file_search_skips_policy_denied_descendants(workspace):
    denied_dir = workspace / "private"
    denied_dir.mkdir()
    (denied_dir / "secret.txt").write_text("sensitive token")
    (workspace / "visible.txt").write_text("public token")
    policy = _DenySubtreePolicy(denied_dir)
    ctx = _FakeCtx(workspace, policy=policy)
    ctx.policy.raw = {"workspace_root": str(workspace)}

    result = _h_search_files({"path": str(workspace), "query": "token"}, ctx)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["path"].endswith("visible.txt")


def test_register_adds_file_search():
    _reset_backend_cache_for_tests()
    registry = ToolRegistry()
    register(registry)
    tools = registry.list()
    assert MODEL_FILE_SEARCH in tools
