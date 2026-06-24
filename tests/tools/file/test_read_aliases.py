from __future__ import annotations

from pathlib import Path

import pytest

from openminion.tools.file.plugin import _h_read_file, _reset_backend_cache_for_tests
from tests.tools.file.test_write import _FakeCtx


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _reset_backend_cache_for_tests()
    return tmp_path


def test_file_read_accepts_file_path_alias(workspace: Path) -> None:
    path = workspace / "report.py"
    path.write_text("hello\n", encoding="utf-8")

    result = _h_read_file({"file_path": str(path)}, _FakeCtx(workspace))

    assert result["ok"] is True
    assert result["content"] == "hello\n"


def test_file_read_accepts_matching_duplicate_path_alias(workspace: Path) -> None:
    path = workspace / "report.py"
    path.write_text("hello\n", encoding="utf-8")

    result = _h_read_file(
        {"path": str(path), "file_path": str(path)}, _FakeCtx(workspace)
    )

    assert result["ok"] is True
    assert result["content"] == "hello\n"
