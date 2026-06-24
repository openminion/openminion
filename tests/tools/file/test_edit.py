from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from openminion.modules.tool.contracts.model_ids import MODEL_FILE_EDIT
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.file.plugin import (
    _h_edit_file,
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


class _FakeCtx:
    def __init__(self, workspace: Path, policy=None):
        self.workspace = workspace
        self.run_root = workspace
        self.scope = "WRITE_SAFE"
        self.confirm = False
        self.env = {}
        self.policy = policy or _AllowPolicy()
        self.policy.raw = {"workspace_root": str(workspace)}


@pytest.fixture()
def workspace():
    _reset_backend_cache_for_tests()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "sample.txt").write_text("hello world\nsecond line\nthird line\n")
        yield p


def test_file_edit_replace_success(workspace):
    f = workspace / "sample.txt"
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {
                    "op": "replace",
                    "old_text": "hello world",
                    "new_text": "hello openminion",
                }
            ],
        },
        ctx,
    )
    assert result["ok"] is True
    assert result["operations_applied"] == 1
    assert "hello openminion" in f.read_text()
    assert "hello world" not in f.read_text()


def test_file_edit_replace_not_found(workspace):
    f = workspace / "sample.txt"
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {"op": "replace", "old_text": "no such text", "new_text": "x"}
            ],
        },
        ctx,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_file_edit_replace_ambiguous(workspace):
    f = workspace / "dup.txt"
    f.write_text("foo bar\nfoo baz\n")
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [{"op": "replace", "old_text": "foo", "new_text": "qux"}],
        },
        ctx,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "AMBIGUOUS"


def test_file_edit_insert_before(workspace):
    f = workspace / "sample.txt"
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {
                    "op": "insert_before",
                    "old_text": "second line",
                    "new_text": "inserted line",
                }
            ],
        },
        ctx,
    )
    assert result["ok"] is True
    content = f.read_text()
    assert content.index("inserted line") < content.index("second line")


def test_file_edit_insert_after(workspace):
    f = workspace / "sample.txt"
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {
                    "op": "insert_after",
                    "old_text": "second line",
                    "new_text": "appended line",
                }
            ],
        },
        ctx,
    )
    assert result["ok"] is True
    content = f.read_text()
    assert content.index("second line") < content.index("appended line")


def test_file_edit_dry_run_no_modification(workspace):
    f = workspace / "sample.txt"
    original = f.read_text()
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {"op": "replace", "old_text": "hello world", "new_text": "changed"}
            ],
            "dry_run": True,
        },
        ctx,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "changed" in result["preview"]
    # File must not be modified
    assert f.read_text() == original


def test_file_edit_multiple_operations(workspace):
    f = workspace / "sample.txt"
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {"op": "replace", "old_text": "hello world", "new_text": "hi there"},
                {"op": "replace", "old_text": "second line", "new_text": "line two"},
            ],
        },
        ctx,
    )
    assert result["ok"] is True
    assert result["operations_applied"] == 2
    content = f.read_text()
    assert "hi there" in content
    assert "line two" in content


def test_file_edit_file_not_found(workspace):
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(workspace / "nonexistent.txt"),
            "operations": [{"op": "replace", "old_text": "x", "new_text": "y"}],
        },
        ctx,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_file_edit_workspace_boundary_enforced(workspace):
    policy = _DenyEscapePolicy(workspace)
    ctx = _FakeCtx(workspace, policy=policy)
    ctx.policy.raw = {"workspace_root": str(workspace)}
    result = _h_edit_file(
        {
            "path": "/etc/passwd",
            "operations": [{"op": "replace", "old_text": "root", "new_text": "x"}],
        },
        ctx,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "POLICY_DENIED"


def test_file_edit_replace_to_empty_is_deletion(workspace):
    f = workspace / "sample.txt"
    ctx = _FakeCtx(workspace)
    result = _h_edit_file(
        {
            "path": str(f),
            "operations": [
                {"op": "replace", "old_text": "hello world\n", "new_text": ""}
            ],
        },
        ctx,
    )
    assert result["ok"] is True
    assert "hello world" not in f.read_text()


def test_register_adds_file_edit():
    _reset_backend_cache_for_tests()
    registry = ToolRegistry()
    register(registry)
    tools = registry.list()
    assert MODEL_FILE_EDIT in tools
