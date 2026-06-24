from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.backends import (
    EditOperation,
    InMemoryStorageBackend,
    SearchMatch,
    StorageBackend,
)


def _backend(tmp_path: Path) -> tuple[InMemoryStorageBackend, Path]:
    workspace = tmp_path / "workspace"
    return InMemoryStorageBackend(root=workspace), workspace


def test_memory_backend_satisfies_protocol(tmp_path: Path):
    backend, _ = _backend(tmp_path)
    assert isinstance(backend, StorageBackend)


def test_memory_backend_list_dir_and_exists_queries(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    backend.write(str(workspace / "alpha.txt"), "alpha")
    backend.write(str(workspace / "nested" / "beta.txt"), "beta")

    result = backend.list_dir(str(workspace))

    assert sorted((entry.name, entry.entry_type) for entry in result.entries) == [
        ("alpha.txt", "file"),
        ("nested", "directory"),
    ]
    assert backend.exists(str(workspace)) is True
    assert backend.is_dir(str(workspace / "nested")) is True
    assert backend.is_file(str(workspace / "alpha.txt")) is True


def test_memory_backend_read_handles_offset_and_truncation(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    backend.write(str(target), "abcdefghij")

    result = backend.read(str(target), max_chars=4, offset=3)

    assert result.content == "defg"
    assert result.total_length == 10
    assert result.returned_length == 4
    assert result.truncated is True


def test_memory_backend_read_rejects_missing_and_non_file(tmp_path: Path):
    backend, workspace = _backend(tmp_path)

    with pytest.raises(ToolRuntimeError) as missing_exc:
        backend.read(str(workspace / "missing.txt"))
    assert missing_exc.value.code == "NOT_FOUND"

    with pytest.raises(ToolRuntimeError) as dir_exc:
        backend.read(str(workspace))
    assert dir_exc.value.code == "INVALID_ARGUMENT"


def test_memory_backend_write_honors_create_dirs_false(tmp_path: Path):
    backend, workspace = _backend(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.write(
            str(workspace / "nested" / "alpha.txt"),
            "hello",
            create_dirs=False,
        )

    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.message == "parent directory does not exist"


def test_memory_backend_find_returns_match_info(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    backend.write(str(workspace / "alpha.txt"), "alpha")
    backend.write(str(workspace / "nested" / "beta.txt"), "beta")
    backend.write(str(workspace / "gamma.md"), "gamma")

    result = backend.find(str(workspace), pattern="*.txt")

    assert sorted(match.name for match in result.matches) == ["alpha.txt", "beta.txt"]
    assert all(hasattr(match, "size") for match in result.matches)


def test_memory_backend_search_returns_search_matches(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    backend.write(str(workspace / "alpha.txt"), "first\nneedle line\nthird\n")

    result = backend.search(str(workspace), query="needle", context_lines=1)

    assert result.count == 1
    assert result.scanned_files == 1
    assert all(isinstance(match, SearchMatch) for match in result.matches)
    assert result.matches[0].path == str(workspace / "alpha.txt")
    assert result.matches[0].line == 2
    assert result.matches[0].snippet == "first\nneedle line\nthird"


def test_memory_backend_search_honors_glob_and_hidden_filter(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    backend.write(str(workspace / "alpha.txt"), "needle")
    backend.write(str(workspace / "beta.md"), "needle")
    backend.write(str(workspace / ".hidden" / "secret.txt"), "needle")

    result = backend.search(str(workspace), query="needle", file_glob="*.txt")

    assert [Path(match.path).name for match in result.matches] == ["alpha.txt"]

    with_hidden = backend.search(
        str(workspace),
        query="needle",
        include_hidden=True,
        file_glob="*.txt",
    )
    assert sorted(Path(match.path).name for match in with_hidden.matches) == [
        "alpha.txt",
        "secret.txt",
    ]


def test_memory_backend_search_invalid_regex(tmp_path: Path):
    backend, workspace = _backend(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.search(str(workspace), query="[", regex=True)

    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert "invalid regex" in excinfo.value.message


def test_memory_backend_search_treats_nul_text_as_searchable(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    backend.write(str(workspace / "nul.txt"), "\x00needle")

    result = backend.search(str(workspace), query="needle")

    assert [Path(match.path).name for match in result.matches] == ["nul.txt"]


def test_memory_backend_edit_applies_operations_and_dry_run(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    backend.write(str(target), "alpha\nbeta\n")

    dry_run = backend.edit(
        str(target),
        [EditOperation(op="insert_before", old_text="beta", new_text="inserted")],
        dry_run=True,
    )

    assert dry_run.preview == "alpha\ninserted\nbeta\n"
    assert backend.read(str(target)).content == "alpha\nbeta\n"

    result = backend.edit(
        str(target),
        [EditOperation(op="insert_after", old_text="alpha", new_text="after")],
    )

    assert result.operations_applied == 1
    assert backend.read(str(target)).content == "alpha\nafter\nbeta\n"


def test_memory_backend_edit_anchor_errors(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    target = workspace / "alpha.txt"
    backend.write(str(target), "alpha\nalpha\n")

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


def test_memory_backend_trash_file_and_directory(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    file_target = workspace / "alpha.txt"
    dir_target = workspace / "nested"
    backend.write(str(file_target), "alpha")
    backend.write(str(dir_target / "beta.txt"), "beta")

    assert backend.trash(str(file_target)) is True
    assert not backend.exists(str(file_target))

    assert backend.trash(str(dir_target)) is True
    assert not backend.exists(str(dir_target))
    assert not backend.exists(str(dir_target / "beta.txt"))


def test_memory_backend_recursive_list_skips_hidden_by_default(tmp_path: Path):
    backend, workspace = _backend(tmp_path)
    backend.write(str(workspace / ".hidden" / "secret.txt"), "secret")
    backend.write(str(workspace / "visible.txt"), "visible")

    result = backend.list_dir(str(workspace), recursive=True)

    assert [entry.name for entry in result.entries] == ["visible.txt"]


def test_memory_backend_root_list_on_empty_workspace(tmp_path: Path):
    backend, workspace = _backend(tmp_path)

    result = backend.list_dir(str(workspace))

    assert result == type(result)(entries=[], count=0)
