from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.backends import (
    EditOperation,
    LocalStorageBackend,
    MatchInfo,
    SearchMatch,
    StorageBackend,
)


def _backend(tmp_path: Path) -> tuple[LocalStorageBackend, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return LocalStorageBackend(workspace), workspace


def test_local_backend_satisfies_protocol(tmp_path: Path):
    backend, _ = _backend(tmp_path)
    assert isinstance(backend, StorageBackend)


def test_local_backend_list_dir_returns_entries(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    (workspace / "alpha.txt").write_text("alpha", encoding="utf-8")
    (workspace / "nested").mkdir()

    result = backend.list_dir(str(workspace))

    names = sorted((entry.name, entry.entry_type) for entry in result.entries)
    assert names == [("alpha.txt", "file"), ("nested", "directory")]
    assert result.count == 2


def test_local_backend_read_handles_offset_and_truncation(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    target.write_text("abcdefghij", encoding="utf-8")

    result = backend.read(str(target), max_chars=4, offset=3)

    assert result.content == "defg"
    assert result.total_length == 10
    assert result.returned_length == 4
    assert result.truncated is True


def test_local_backend_read_rejects_binary_input(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.bin"
    target.write_bytes(b"\xff\xfe")

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.read(str(target))

    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert excinfo.value.message == "file is not text-encoded"


def test_local_backend_write_honors_create_dirs_false(tmp_path: Path):
    backend, workspace = _backend(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.write(
            str(workspace / "nested" / "alpha.txt"),
            "hello",
            create_dirs=False,
        )

    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.message == "parent directory does not exist"


def test_local_backend_find_returns_match_info(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    nested = workspace / "nested"
    nested.mkdir()
    (workspace / "alpha.txt").write_text("alpha", encoding="utf-8")
    (nested / "beta.txt").write_text("beta", encoding="utf-8")
    (workspace / "gamma.md").write_text("gamma", encoding="utf-8")

    result = backend.find(str(workspace), pattern="*.txt")

    assert result.count == 2
    assert all(isinstance(match, MatchInfo) for match in result.matches)
    assert sorted(match.name for match in result.matches) == ["alpha.txt", "beta.txt"]


def test_local_backend_search_returns_search_matches(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    target.write_text("first\nneedle line\nthird\n", encoding="utf-8")

    result = backend.search(
        str(workspace),
        query="needle",
        context_lines=1,
    )

    assert result.count == 1
    assert result.scanned_files == 1
    assert all(isinstance(match, SearchMatch) for match in result.matches)
    assert result.matches[0].path == str(target)
    assert result.matches[0].line == 2
    assert result.matches[0].snippet == "first\nneedle line\nthird"


def test_local_backend_search_skips_binary_file(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    (workspace / "binary.bin").write_bytes(b"\x00needle")
    (workspace / "text.txt").write_text("needle", encoding="utf-8")

    result = backend.search(str(workspace), query="needle")

    assert [Path(match.path).name for match in result.matches] == ["text.txt"]
    assert result.scanned_files == 1


def test_local_backend_search_invalid_regex(tmp_path: Path):
    backend, workspace = _backend(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.search(str(workspace), query="[", regex=True)

    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert "invalid regex" in excinfo.value.message


def test_local_backend_edit_applies_operations_and_dry_run(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    dry_run = backend.edit(
        str(target),
        [EditOperation(op="insert_after", old_text="alpha", new_text="inserted")],
        dry_run=True,
    )

    assert dry_run.preview == "alpha\ninserted\nbeta\n"
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    result = backend.edit(
        str(target),
        [EditOperation(op="replace", old_text="beta", new_text="gamma")],
    )

    assert result.operations_applied == 1
    assert result.dry_run is False
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_local_backend_edit_anchor_errors(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    target.write_text("alpha\nalpha\n", encoding="utf-8")

    with pytest.raises(ToolRuntimeError) as missing_exc:
        backend.edit(
            str(target),
            [EditOperation(op="replace", old_text="missing", new_text="x")],
        )
    assert missing_exc.value.code == "NOT_FOUND"

    with pytest.raises(ToolRuntimeError) as ambiguous_exc:
        backend.edit(
            str(target),
            [EditOperation(op="replace", old_text="alpha", new_text="x")],
        )
    assert ambiguous_exc.value.code == "AMBIGUOUS"


def test_local_backend_trash_file_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backend, workspace = _backend(tmp_path)
    home = tmp_path / "home"
    trash_dir = home / ".Trash"
    trash_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    file_target = workspace / "alpha.txt"
    file_target.write_text("alpha", encoding="utf-8")
    assert backend.trash(str(file_target)) is True
    assert not file_target.exists()

    dir_target = workspace / "nested"
    dir_target.mkdir()
    (dir_target / "beta.txt").write_text("beta", encoding="utf-8")
    assert backend.trash(str(dir_target)) is True
    assert not dir_target.exists()
    assert (trash_dir / "nested" / "beta.txt").read_text(encoding="utf-8") == "beta"


def test_local_backend_exists_and_type_queries(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    nested = workspace / "nested"
    nested.mkdir()
    file_target = workspace / "alpha.txt"
    file_target.write_text("alpha", encoding="utf-8")

    assert backend.exists(str(workspace)) is True
    assert backend.is_dir(str(nested)) is True
    assert backend.is_file(str(file_target)) is True
    assert backend.exists(str(workspace / "missing.txt")) is False
