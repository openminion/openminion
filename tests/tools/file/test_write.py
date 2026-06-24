from __future__ import annotations

from pathlib import Path

import pytest

from openminion.tools.file.plugin import _h_write_file, _reset_backend_cache_for_tests


class _AllowPolicy:
    raw: dict = {}

    def ensure_path_allowed(self, path, *, workspace, operation):
        del workspace, operation
        return Path(path).resolve()

    def limit_int(self, key, default):
        del key
        return default


class _FakeCtx:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.run_root = workspace
        self.scope = "WRITE_SAFE"
        self.confirm = True
        self.env = {}
        self.policy = _AllowPolicy()
        self.policy.raw = {"workspace_root": str(workspace)}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _reset_backend_cache_for_tests()
    return tmp_path


def test_file_write_accepts_text_alias(workspace: Path) -> None:
    result = _h_write_file(
        {"path": str(workspace / "notes.txt"), "text": "hello"},
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_file_write_accepts_filename_alias(workspace: Path) -> None:
    result = _h_write_file(
        {"filename": str(workspace / "README.md"), "content": "# ok\n"},
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert (workspace / "README.md").read_text(encoding="utf-8") == "# ok\n"


def test_file_write_accepts_destination_alias(workspace: Path) -> None:
    result = _h_write_file(
        {"destination": str(workspace / "tests" / "test_report.py"), "content": "ok"},
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert (workspace / "tests" / "test_report.py").read_text(encoding="utf-8") == "ok"


def test_file_write_accepts_body_alias_with_duplicate_path_alias(
    workspace: Path,
) -> None:
    path = workspace / "report.py"
    result = _h_write_file(
        {"path": str(path), "file_path": str(path), "body": "print('ok')\n"},
        _FakeCtx(workspace),
    )

    assert result["ok"] is True
    assert path.read_text(encoding="utf-8") == "print('ok')\n"
