from __future__ import annotations

from pathlib import Path

import pytest

from openminion.tools.file.backends.memory import InMemoryStorageBackend
from openminion.tools.file.plugin import (
    _h_copy,
    _h_mkdir,
    _reset_backend_cache_for_tests,
)
from openminion.modules.tool.errors import ToolRuntimeError


class _AllowPolicy:
    raw: dict = {}

    def ensure_path_allowed(self, path, *, workspace, operation):
        del workspace, operation
        return Path(path).resolve()

    def limit_int(self, key, default):
        del key
        return default


class _DenyPolicy(_AllowPolicy):
    def ensure_path_allowed(self, path, *, workspace, operation):
        raise ToolRuntimeError("POLICY_DENIED", f"{operation} outside workspace")


class _FakeCtx:
    def __init__(self, workspace: Path, policy=None):
        self.workspace = workspace
        self.run_root = workspace
        self.scope = "WRITE_SAFE"
        self.confirm = True
        self.env = {}
        self.policy = policy or _AllowPolicy()
        self.policy.raw = {"workspace_root": str(workspace)}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _reset_backend_cache_for_tests()
    return tmp_path


def test_mkdir_creates_the_directory(workspace: Path) -> None:
    result = _h_mkdir({"path": str(workspace / "build")}, _FakeCtx(workspace))

    assert result["ok"] is True
    assert result["created"] is True
    assert (workspace / "build").is_dir()


def test_mkdir_reports_an_existing_directory_without_error(workspace: Path) -> None:
    (workspace / "build").mkdir()

    result = _h_mkdir({"path": str(workspace / "build")}, _FakeCtx(workspace))

    assert result["ok"] is True
    assert result["created"] is False


def test_mkdir_requires_parents_for_a_nested_path(workspace: Path) -> None:
    result = _h_mkdir({"path": str(workspace / "a" / "b")}, _FakeCtx(workspace))

    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_mkdir_creates_nested_paths_when_asked(workspace: Path) -> None:
    result = _h_mkdir(
        {"path": str(workspace / "a" / "b"), "parents": True}, _FakeCtx(workspace)
    )

    assert result["ok"] is True
    assert (workspace / "a" / "b").is_dir()


def test_mkdir_refuses_a_path_that_is_a_file(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("x", encoding="utf-8")

    result = _h_mkdir({"path": str(workspace / "notes.txt")}, _FakeCtx(workspace))

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_mkdir_maps_a_denied_path(workspace: Path) -> None:
    result = _h_mkdir(
        {"path": str(workspace / "build")}, _FakeCtx(workspace, _DenyPolicy())
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "POLICY_DENIED"


def test_copy_writes_the_same_bytes(workspace: Path) -> None:
    (workspace / "src.txt").write_text("payload", encoding="utf-8")

    result = _h_copy(
        {
            "source": str(workspace / "src.txt"),
            "destination": str(workspace / "dst.txt"),
        },
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert result["bytes"] == len("payload")
    assert (workspace / "dst.txt").read_text(encoding="utf-8") == "payload"


def test_copy_creates_the_destination_parents(workspace: Path) -> None:
    (workspace / "src.txt").write_text("payload", encoding="utf-8")

    result = _h_copy(
        {
            "source": str(workspace / "src.txt"),
            "destination": str(workspace / "out" / "dst.txt"),
        },
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert (workspace / "out" / "dst.txt").is_file()


def test_copy_preserves_a_destination_created_during_parent_preparation(
    workspace: Path, monkeypatch
) -> None:
    (workspace / "src.txt").write_text("ours", encoding="utf-8")
    destination = workspace / "out" / "dst.txt"
    real_mkdir = Path.mkdir

    def mkdir_then_compete(target, *args, **kwargs):
        created = real_mkdir(target, *args, **kwargs)
        if target == workspace / "out":
            destination.write_text("theirs", encoding="utf-8")
        return created

    monkeypatch.setattr(Path, "mkdir", mkdir_then_compete)

    result = _h_copy(
        {"source": str(workspace / "src.txt"), "destination": str(destination)},
        _FakeCtx(workspace),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "PRECONDITION_FAILED"
    assert destination.read_text(encoding="utf-8") == "theirs"


def test_copy_follows_a_symlinked_source_through_the_shared_resolver(
    workspace: Path,
) -> None:
    (workspace / "real.txt").write_text("target bytes", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "real.txt")

    result = _h_copy(
        {
            "source": str(workspace / "link.txt"),
            "destination": str(workspace / "dst.txt"),
        },
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert (workspace / "dst.txt").read_text(encoding="utf-8") == "target bytes"


def test_readonly_blocked_inventory_covers_both_tools() -> None:
    from openminion.modules.brain.adapters.tool.permission_mode import (
        readonly_blocked_tool_names,
    )

    assert {"file.mkdir", "file.copy"} <= readonly_blocked_tool_names()


def test_copy_refuses_an_existing_destination(workspace: Path) -> None:
    (workspace / "src.txt").write_text("new", encoding="utf-8")
    (workspace / "dst.txt").write_text("old", encoding="utf-8")

    result = _h_copy(
        {
            "source": str(workspace / "src.txt"),
            "destination": str(workspace / "dst.txt"),
        },
        _FakeCtx(workspace),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "PRECONDITION_FAILED"
    assert (workspace / "dst.txt").read_text(encoding="utf-8") == "old"


def test_copy_overwrites_only_when_asked(workspace: Path) -> None:
    (workspace / "src.txt").write_text("new", encoding="utf-8")
    (workspace / "dst.txt").write_text("old", encoding="utf-8")

    result = _h_copy(
        {
            "source": str(workspace / "src.txt"),
            "destination": str(workspace / "dst.txt"),
            "overwrite": True,
        },
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert (workspace / "dst.txt").read_text(encoding="utf-8") == "new"


def test_copy_refuses_a_directory_source(workspace: Path) -> None:
    (workspace / "build").mkdir()

    result = _h_copy(
        {"source": str(workspace / "build"), "destination": str(workspace / "dst.txt")},
        _FakeCtx(workspace),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_copy_refuses_a_missing_source(workspace: Path) -> None:
    result = _h_copy(
        {
            "source": str(workspace / "gone.txt"),
            "destination": str(workspace / "dst.txt"),
        },
        _FakeCtx(workspace),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_memory_backend_rejects_directories_and_copies() -> None:
    backend = InMemoryStorageBackend()

    with pytest.raises(ToolRuntimeError, match="directories are unavailable"):
        backend.mkdir("/build", parents=True)
    with pytest.raises(ToolRuntimeError, match="file copies are unavailable"):
        backend.copy_file("/a", "/b", overwrite=True)
