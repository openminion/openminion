from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from unittest import mock

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.file.constants import FILE_MAX_WRITE_CHARS
from openminion.tools.file.plugin import (
    FileReadRangeArgs,
    _h_edit_file,
    _h_find_files,
    _h_list_dir,
    _h_read_file,
    _h_read_range,
    _h_trash,
    _h_write_file,
)


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def test_list_dir_non_recursive_returns_entries(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("alpha", encoding="utf-8")
    (ctx.workspace / "nested").mkdir()

    result = _h_list_dir({"path": "."}, ctx)

    assert result["ok"] is True
    assert result["path"] == str(ctx.workspace)
    names = sorted((entry["name"], entry["type"]) for entry in result["entries"])
    assert names == [("alpha.txt", "file"), ("nested", "directory")]
    assert result["count"] == 2


def test_list_dir_recursive_skips_hidden_by_default(tmp_path: Path):
    ctx = _ctx(tmp_path)
    nested = ctx.workspace / "nested"
    nested.mkdir()
    (nested / "visible.txt").write_text("ok", encoding="utf-8")
    hidden_dir = ctx.workspace / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("secret", encoding="utf-8")
    (ctx.workspace / ".env").write_text("env", encoding="utf-8")

    result = _h_list_dir({"path": ".", "recursive": True}, ctx)

    assert result["ok"] is True
    names = sorted(entry["name"] for entry in result["entries"])
    assert names == ["nested", "visible.txt"]
    assert result["count"] == 2


def test_list_dir_returns_not_found_for_missing_path(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_list_dir({"path": "missing"}, ctx)

    assert result == {
        "ok": False,
        "error": {"code": "NOT_FOUND", "message": "path does not exist: missing"},
        "entries": [],
    }


def test_list_dir_rejects_non_directory(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("alpha", encoding="utf-8")

    result = _h_list_dir({"path": "alpha.txt"}, ctx)

    assert result == {
        "ok": False,
        "error": {"code": "INVALID_ARGUMENT", "message": "path is not a directory"},
        "entries": [],
    }


def test_read_file_returns_content(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("alpha beta", encoding="utf-8")

    result = _h_read_file({"path": "alpha.txt"}, ctx)

    assert result == {
        "ok": True,
        "path": str(ctx.workspace / "alpha.txt"),
        "content": "alpha beta",
        "truncated": False,
        "total_length": 10,
        "returned_length": 10,
        "source": "file_module",
    }


def test_read_file_applies_offset_and_truncation(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("abcdefghij", encoding="utf-8")

    result = _h_read_file({"path": "alpha.txt", "offset": 3, "max_chars": 4}, ctx)

    assert result == {
        "ok": True,
        "path": str(ctx.workspace / "alpha.txt"),
        "content": "defg",
        "truncated": True,
        "total_length": 10,
        "returned_length": 4,
        "source": "file_module",
    }


def test_read_file_rejects_offset_beyond_length(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("abc", encoding="utf-8")

    result = _h_read_file({"path": "alpha.txt", "offset": 3}, ctx)

    assert result == {
        "ok": False,
        "error": {
            "code": "INVALID_ARGUMENT",
            "message": "offset beyond file length",
        },
    }


def test_read_file_returns_not_found_for_missing_path(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_read_file({"path": "missing.txt"}, ctx)

    assert result == {
        "ok": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "path does not exist: missing.txt",
        },
    }


def test_read_file_rejects_non_file(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "nested").mkdir()

    result = _h_read_file({"path": "nested"}, ctx)

    assert result == {
        "ok": False,
        "error": {"code": "INVALID_ARGUMENT", "message": "path is not a file"},
    }


def test_read_range_returns_numbered_snippet(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")

    result = _h_read_range(
        {"path": "alpha.txt", "start_line": 2, "end_line": 3},
        ctx,
    )

    assert result == {
        "ok": True,
        "path": str(ctx.workspace / "alpha.txt"),
        "start_line": 2,
        "end_line": 3,
        "total_lines": 4,
        "content": "2: b\n3: c",
        "truncated": False,
        "source": "file_module",
    }


def test_read_range_accepts_camel_case_line_aliases(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")

    result = _h_read_range(
        {"path": "alpha.txt", "startLine": "2", "endLine": "3"},
        ctx,
    )

    assert result == {
        "ok": True,
        "path": str(ctx.workspace / "alpha.txt"),
        "start_line": 2,
        "end_line": 3,
        "total_lines": 4,
        "content": "2: b\n3: c",
        "truncated": False,
        "source": "file_module",
    }


def test_read_range_rejects_unexpected_extra_keys() -> None:
    with pytest.raises(ValidationError):
        FileReadRangeArgs.model_validate(
            {
                "path": "alpha.txt",
                "startLine": 1,
                "endLine": 2,
                "lineCount": 2,
            }
        )


def test_read_range_clamps_out_of_bounds_end_line(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("a\nb\n", encoding="utf-8")

    result = _h_read_range(
        {"path": "alpha.txt", "start_line": 2, "end_line": 99},
        ctx,
    )

    assert result["ok"] is True
    assert result["start_line"] == 2
    assert result["end_line"] == 2
    assert result["content"] == "2: b"


def test_read_range_returns_not_found_for_missing_path(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_read_range({"path": "missing.txt", "start_line": 1, "end_line": 5}, ctx)

    assert result == {
        "ok": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "path does not exist: missing.txt",
        },
    }


def test_write_file_creates_file(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_write_file({"path": "alpha.txt", "content": "hello"}, ctx)

    assert result == {
        "ok": True,
        "path": str(ctx.workspace / "alpha.txt"),
        "bytes_written": 5,
        "mode": "write",
        "source": "file_module",
    }
    assert (ctx.workspace / "alpha.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_appends_when_requested(tmp_path: Path):
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.txt"
    target.write_text("hello", encoding="utf-8")

    result = _h_write_file(
        {"path": "alpha.txt", "content": " world", "append": True}, ctx
    )

    assert result == {
        "ok": True,
        "path": str(target),
        "bytes_written": 6,
        "mode": "append",
        "source": "file_module",
    }
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_file_requires_parent_when_create_dirs_disabled(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_write_file(
        {"path": "nested/alpha.txt", "content": "hello", "create_dirs": False},
        ctx,
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "parent directory does not exist",
        },
    }


def test_write_file_rejects_content_over_limit(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_write_file(
        {"path": "alpha.txt", "content": "x" * (FILE_MAX_WRITE_CHARS + 1)}, ctx
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "content exceeds maximum" in result["error"]["message"]


def test_find_files_returns_matches(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("alpha", encoding="utf-8")
    (ctx.workspace / "beta.md").write_text("beta", encoding="utf-8")
    nested = ctx.workspace / "nested"
    nested.mkdir()
    (nested / "gamma.txt").write_text("gamma", encoding="utf-8")

    result = _h_find_files({"path": ".", "pattern": "*.txt"}, ctx)

    assert result["ok"] is True
    assert result["path"] == str(ctx.workspace)
    assert result["pattern"] == "*.txt"
    names = sorted(match["name"] for match in result["matches"])
    assert names == ["alpha.txt", "gamma.txt"]
    assert all("entry_type" not in match for match in result["matches"])
    assert result["count"] == 2


def test_find_files_skips_hidden_by_default(tmp_path: Path):
    ctx = _ctx(tmp_path)
    hidden = ctx.workspace / ".hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("secret", encoding="utf-8")
    (ctx.workspace / "visible.txt").write_text("visible", encoding="utf-8")

    result = _h_find_files({"path": ".", "pattern": "*.txt"}, ctx)

    assert result["ok"] is True
    assert [match["name"] for match in result["matches"]] == ["visible.txt"]


def test_find_files_returns_not_found_for_missing_path(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_find_files({"path": "missing", "pattern": "*.txt"}, ctx)

    assert result == {
        "ok": False,
        "error": {"code": "NOT_FOUND", "message": "path does not exist: missing"},
        "matches": [],
    }


def test_find_files_rejects_non_directory(tmp_path: Path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.txt").write_text("alpha", encoding="utf-8")

    result = _h_find_files({"path": "alpha.txt", "pattern": "*.txt"}, ctx)

    assert result == {
        "ok": False,
        "error": {"code": "INVALID_ARGUMENT", "message": "path is not a directory"},
        "matches": [],
    }


def test_trash_file_removes_target(tmp_path: Path):
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.txt"
    target.write_text("alpha", encoding="utf-8")

    result = _h_trash({"path": "alpha.txt"}, ctx)

    assert result == {
        "ok": True,
        "path": str(target),
        "trashed": True,
        "source": "file_module",
    }
    assert not target.exists()


def test_trash_directory_moves_to_home_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    trash_dir = home / ".Trash"
    trash_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    ctx = _ctx(tmp_path)
    target = ctx.workspace / "nested"
    target.mkdir()
    (target / "alpha.txt").write_text("alpha", encoding="utf-8")

    result = _h_trash({"path": "nested"}, ctx)

    assert result == {
        "ok": True,
        "path": str(target),
        "trashed": True,
        "source": "file_module",
    }
    assert not target.exists()
    moved = trash_dir / "nested"
    assert moved.exists()
    assert (moved / "alpha.txt").read_text(encoding="utf-8") == "alpha"


def test_trash_returns_not_found_for_missing_path(tmp_path: Path):
    ctx = _ctx(tmp_path)

    result = _h_trash({"path": "missing"}, ctx)

    assert result == {
        "ok": False,
        "error": {"code": "NOT_FOUND", "message": "path does not exist: missing"},
    }


def test_edit_file_keeps_exec_error_on_read_failure(tmp_path: Path):
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.txt"
    target.write_text("alpha", encoding="utf-8")

    with mock.patch.object(Path, "read_text", side_effect=OSError("boom")):
        result = _h_edit_file(
            {
                "path": "alpha.txt",
                "operations": [
                    {"op": "replace", "old_text": "alpha", "new_text": "beta"}
                ],
            },
            ctx,
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "EXEC_ERROR"
    assert result["error"]["message"] == "boom"
