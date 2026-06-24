from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.backends import (
    EditOperation,
    InMemoryStorageBackend,
    LocalStorageBackend,
    SearchResult,
    StorageBackend,
)


def _make_backend(
    backend_kind: str,
    tmp_path: Path,
    *,
    initial_files: dict[str, str] | None = None,
) -> tuple[StorageBackend, Path]:
    workspace = tmp_path / backend_kind / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    files = initial_files or {}
    if backend_kind == "local":
        backend: StorageBackend = LocalStorageBackend(workspace)
        for rel_path, content in files.items():
            target = workspace / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    else:
        backend = InMemoryStorageBackend(root=workspace)
        for rel_path, content in files.items():
            backend.write(str(workspace / rel_path), content)
    return backend, workspace


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_list_dir_matches_shape(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "alpha", "nested/beta.txt": "beta"},
    )

    result = backend.list_dir(str(workspace))

    payload = sorted((entry.name, entry.entry_type) for entry in result.entries)
    assert payload == [("alpha.txt", "file"), ("nested", "directory")]
    assert result.count == 2


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_read_matches_shape(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "abcdefghij"},
    )

    result = backend.read(str(workspace / "alpha.txt"), max_chars=4, offset=3)

    assert asdict(result) == {
        "content": "defg",
        "total_length": 10,
        "returned_length": 4,
        "truncated": True,
    }


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_read_missing_raises_not_found(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(backend_kind, tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.read(str(workspace / "missing.txt"))

    assert excinfo.value.code == "NOT_FOUND"


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_write_matches_shape(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(backend_kind, tmp_path)

    result = backend.write(str(workspace / "alpha.txt"), "alpha")

    assert asdict(result) == {
        "path": str(workspace / "alpha.txt"),
        "bytes_written": 5,
        "mode": "write",
    }


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_write_append_matches_shape(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "alpha"},
    )

    result = backend.write(str(workspace / "alpha.txt"), "beta", append=True)

    assert result.mode == "append"
    read_back = backend.read(str(workspace / "alpha.txt"))
    assert read_back.content == "alphabeta"


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_write_missing_parent_raises_not_found(
    tmp_path: Path, backend_kind: str
):
    backend, workspace = _make_backend(backend_kind, tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.write(
            str(workspace / "nested" / "alpha.txt"),
            "alpha",
            create_dirs=False,
        )

    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.message == "parent directory does not exist"


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_find_matches_shape(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={
            "alpha.txt": "alpha",
            "nested/beta.txt": "beta",
            "gamma.md": "gamma",
        },
    )

    result = backend.find(str(workspace), pattern="*.txt")

    assert [sorted(match.keys()) for match in map(asdict, result.matches)] == [
        ["name", "path", "size"],
        ["name", "path", "size"],
    ]
    assert sorted(match.name for match in result.matches) == ["alpha.txt", "beta.txt"]
    assert result.count == 2


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_trash_file_removes_entry(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "alpha"},
    )

    assert backend.trash(str(workspace / "alpha.txt")) is True
    assert backend.exists(str(workspace / "alpha.txt")) is False


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_trash_directory_removes_tree(
    tmp_path: Path,
    backend_kind: str,
    monkeypatch: pytest.MonkeyPatch,
):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"nested/alpha.txt": "alpha"},
    )
    if backend_kind == "local":
        home = tmp_path / "home"
        trash_dir = home / ".Trash"
        trash_dir.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))

    assert backend.trash(str(workspace / "nested")) is True
    assert backend.exists(str(workspace / "nested")) is False
    assert backend.exists(str(workspace / "nested" / "alpha.txt")) is False


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_exists_is_file_is_dir(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"nested/alpha.txt": "alpha"},
    )

    assert backend.exists(str(workspace)) is True
    assert backend.is_dir(str(workspace / "nested")) is True
    assert backend.is_file(str(workspace / "nested" / "alpha.txt")) is True
    assert backend.exists(str(workspace / "missing.txt")) is False


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_recursive_operations_skip_hidden_descendants_by_default(
    tmp_path: Path,
    backend_kind: str,
):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={
            ".hidden/secret.txt": "secret",
            "visible.txt": "visible",
        },
    )

    listed = backend.list_dir(str(workspace), recursive=True)
    found = backend.find(str(workspace), pattern="*.txt")

    assert [entry.name for entry in listed.entries] == ["visible.txt"]
    assert [match.name for match in found.matches] == ["visible.txt"]


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_search_literal_match(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "first\nneedle line\nthird\n"},
    )

    result = backend.search(str(workspace), query="needle")

    assert isinstance(result, SearchResult)
    assert result.count == 1
    assert result.matches[0].path == str(workspace / "alpha.txt")
    assert result.matches[0].line == 2
    assert result.matches[0].snippet == "needle line"


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_search_regex_match(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.py": "def alpha():\n    return 1\n"},
    )

    result = backend.search(str(workspace), query=r"def \w+", regex=True)

    assert result.count == 1
    assert result.matches[0].line == 1


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_search_case_sensitive(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "Needle\nneedle\n"},
    )

    insensitive = backend.search(str(workspace), query="NEEDLE")
    sensitive = backend.search(str(workspace), query="NEEDLE", case_sensitive=True)

    assert insensitive.count == 2
    assert sensitive.count == 0


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_search_context_lines(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "before\nneedle\nafter\n"},
    )

    result = backend.search(str(workspace), query="needle", context_lines=1)

    assert result.matches[0].snippet == "before\nneedle\nafter"


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_edit_anchor_replace(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "alpha\nbeta\n"},
    )

    result = backend.edit(
        str(workspace / "alpha.txt"),
        [EditOperation(op="replace", old_text="beta", new_text="gamma")],
    )

    assert result.path == str(workspace / "alpha.txt")
    assert result.operations_applied == 1
    assert result.dry_run is False
    assert backend.read(str(workspace / "alpha.txt")).content == "alpha\ngamma\n"


@pytest.mark.parametrize("backend_kind", ["local", "memory"])
def test_contract_edit_anchor_not_found(tmp_path: Path, backend_kind: str):
    backend, workspace = _make_backend(
        backend_kind,
        tmp_path,
        initial_files={"alpha.txt": "alpha\n"},
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.edit(
            str(workspace / "alpha.txt"),
            [EditOperation(op="replace", old_text="missing", new_text="gamma")],
        )

    assert excinfo.value.code == "NOT_FOUND"


def test_contract_memory_search_does_not_skip_nul_text(tmp_path: Path):
    backend, workspace = _make_backend(
        "memory",
        tmp_path,
        initial_files={"nul.txt": "\x00needle"},
    )

    result = backend.search(str(workspace), query="needle")

    assert result.count == 1


def test_contract_memory_has_no_symlink_semantics(tmp_path: Path):
    backend, workspace = _make_backend(
        "memory",
        tmp_path,
        initial_files={"link.txt": "needle"},
    )

    result = backend.search(str(workspace), query="needle")

    assert result.count == 1
    assert result.matches[0].path == str(workspace / "link.txt")


def test_contract_memory_has_no_local_byte_ceiling(tmp_path: Path):
    backend, workspace = _make_backend(
        "memory",
        tmp_path,
        initial_files={"large.txt": "x" * 210000 + "needle"},
    )

    result = backend.search(str(workspace), query="needle")

    assert result.count == 1


def test_contract_local_search_skips_binary_large_and_symlink_inputs(
    tmp_path: Path,
):
    backend, workspace = _make_backend(
        "local",
        tmp_path,
        initial_files={"plain.txt": "needle"},
    )
    (workspace / "binary.bin").write_bytes(b"\x00needle")
    (workspace / "large.txt").write_text("x" * 210000 + "needle", encoding="utf-8")
    target = workspace / "target.txt"
    target.write_text("needle", encoding="utf-8")
    (workspace / "link.txt").symlink_to(target)

    result = backend.search(str(workspace), query="needle")

    assert sorted(Path(match.path).name for match in result.matches) == [
        "plain.txt",
        "target.txt",
    ]
