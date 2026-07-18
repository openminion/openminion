from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from openminion.cli.interactive.files import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_FILE_SIZE,
    build_file_index,
)


def _touch(path: Path, content: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_walker_returns_relative_paths_sorted_alphabetically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root / "z_last.py")
        _touch(root / "a_first.py")
        _touch(root / "src" / "module.py")
        _touch(root / "src" / "alpha.py")

        index = build_file_index(root)

        relatives = [rel for rel, _abs in index]
        assert relatives == [
            "a_first.py",
            "src/alpha.py",
            "src/module.py",
            "z_last.py",
        ], relatives


def test_walker_skips_each_default_ignored_directory() -> None:
    from openminion.cli.interactive.files import _DEFAULT_IGNORE_DIRS

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root / "real.py")
        for ignored in _DEFAULT_IGNORE_DIRS:
            _touch(root / ignored / "should_not_appear.txt")

        index = build_file_index(root)
        relatives = [rel for rel, _abs in index]

        assert "real.py" in relatives
        for ignored in _DEFAULT_IGNORE_DIRS:
            for rel in relatives:
                assert not rel.startswith(f"{ignored}/"), (
                    f"file under ignored dir {ignored!r} leaked into index: {rel!r}"
                )


def test_walker_respects_max_depth() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root / "lvl0.py")
        _touch(root / "a" / "lvl1.py")
        _touch(root / "a" / "b" / "lvl2.py")
        _touch(root / "a" / "b" / "c" / "lvl3.py")

        index = build_file_index(root, max_depth=2)
        relatives = [rel for rel, _abs in index]

        assert "lvl0.py" in relatives
        assert "a/lvl1.py" in relatives
        assert "a/b/lvl2.py" in relatives
        assert "a/b/c/lvl3.py" not in relatives, (
            "max_depth=2 should exclude depth-3 entries"
        )


def test_walker_skips_files_larger_than_max_size() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root / "small.txt", b"x" * 10)
        _touch(root / "big.bin", b"x" * 100)

        index = build_file_index(root, max_file_size=50)
        relatives = [rel for rel, _abs in index]

        assert "small.txt" in relatives
        assert "big.bin" not in relatives


def test_walker_caps_total_results_at_max_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i in range(10):
            _touch(root / f"f{i:02d}.py")

        index = build_file_index(root, max_files=3)
        assert len(index) == 3, f"expected exactly 3 files, got {len(index)}"


def test_walker_returns_empty_for_missing_or_non_directory() -> None:
    assert build_file_index("/this/path/does/not/exist/anywhere") == []
    with tempfile.NamedTemporaryFile() as tmp_file:
        assert build_file_index(tmp_file.name) == []


def test_walker_uses_forward_slashes_for_relative_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root / "src" / "deep" / "module.py")

        index = build_file_index(root)
        relatives = [rel for rel, _abs in index]

        # On Linux/macOS this is naturally forward-slash; on Windows
        # we'd produce `src\\deep\\module.py` without normalisation.
        # The replacement step (`.replace(os.sep, "/")`) covers both.
        assert "src/deep/module.py" in relatives


def test_walker_returns_absolute_paths_alongside_relatives() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        _touch(root / "hello.txt")

        index = build_file_index(root)
        assert index, "expected at least one file"
        rel, absolute = index[0]
        assert rel == "hello.txt"
        assert os.path.isabs(absolute), absolute
        assert Path(absolute).exists()


def test_walker_skips_symlinks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outside = Path(tempfile.mkdtemp())
        try:
            _touch(outside / "outside_target.txt", b"escape")
            _touch(root / "inside.py")
            try:
                (root / "outside_link").symlink_to(outside / "outside_target.txt")
            except (OSError, NotImplementedError):
                pytest.skip("platform does not support symlinks in this test env")

            index = build_file_index(root)
            relatives = [rel for rel, _abs in index]
            assert "inside.py" in relatives
            assert "outside_link" not in relatives
        finally:
            import shutil

            shutil.rmtree(outside, ignore_errors=True)


def test_walker_default_constants_match_spec() -> None:
    assert DEFAULT_MAX_FILES == 5000
    assert DEFAULT_MAX_DEPTH == 6
    assert DEFAULT_MAX_FILE_SIZE == 1_048_576
