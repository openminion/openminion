from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = REPO_ROOT / "openminion" / "scripts" / "validate/chat_import_boundaries.py"


@pytest.fixture(scope="module")
def guard_module():
    spec = importlib.util.spec_from_file_location("cmsg_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cmsg_guard"] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(tmp_path: Path, body: str, *, name: str = "fixture.py") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_runs_clean_when_chat_dir_missing(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_chat_dir = tmp_path / "chat"
    fake_baseline = tmp_path / "baseline.txt"
    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "CHAT_DIR", fake_chat_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)
    assert guard_module.main([]) == 0


def test_detects_importfrom_modules_submodule(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from openminion.modules.session.todo import Todo, get_default_todo_store\n",
    )
    violations = guard_module._scan_file(fixture)
    assert [v.as_baseline_line() for v in violations] == [
        f"{fixture.as_posix()}:1:openminion.modules.session.todo:Todo",
        f"{fixture.as_posix()}:1:openminion.modules.session.todo:get_default_todo_store",
    ]


def test_detects_plain_import_modules_submodule(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path, "import openminion.modules.foo\n")
    violations = guard_module._scan_file(fixture)
    assert len(violations) == 1
    assert violations[0].module == "openminion.modules.foo"
    assert violations[0].symbol is None


def test_does_not_detect_services_imports(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from openminion.services.gateway.service import GatewayService\n",
    )
    assert guard_module._scan_file(fixture) == []


def test_does_not_detect_bare_openminion_modules_importfrom(
    guard_module, tmp_path: Path
) -> None:
    fixture = _write_fixture(tmp_path, "from openminion.modules import alpha\n")
    assert guard_module._scan_file(fixture) == []


def test_baseline_match_passes(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_chat_dir = tmp_path / "chat"
    fake_chat_dir.mkdir()
    fixture = _write_fixture(
        fake_chat_dir,
        "from openminion.modules.session.todo import Todo\n",
        name="runner.py",
    )
    rel = fixture.relative_to(tmp_path).as_posix()
    fake_baseline = tmp_path / "baseline.txt"
    fake_baseline.write_text(
        f"# header\n{rel}:1:openminion.modules.session.todo:Todo\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "CHAT_DIR", fake_chat_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)
    assert guard_module.main([]) == 0


def test_new_violation_not_in_baseline_fails(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_chat_dir = tmp_path / "chat"
    fake_chat_dir.mkdir()
    fixture = _write_fixture(
        fake_chat_dir,
        "from openminion.modules.session.todo import Todo\n",
        name="runner.py",
    )
    rel = fixture.relative_to(tmp_path).as_posix()
    fake_baseline = tmp_path / "baseline.txt"
    fake_baseline.write_text("", encoding="utf-8")

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "CHAT_DIR", fake_chat_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)
    assert guard_module.main([]) == 1
    assert rel == "chat/runner.py"


def test_stale_baseline_fails(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_chat_dir = tmp_path / "chat"
    fake_chat_dir.mkdir()
    fake_baseline = tmp_path / "baseline.txt"
    fake_baseline.write_text(
        "chat/runner.py:1:openminion.modules.session.todo:Todo\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "CHAT_DIR", fake_chat_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)
    assert guard_module.main([]) == 1


def test_update_baseline_writes_expected_format(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_chat_dir = tmp_path / "chat"
    fake_chat_dir.mkdir()
    _write_fixture(
        fake_chat_dir,
        "from openminion.modules.session.todo import Todo\nimport openminion.modules.foo\n",
        name="runner.py",
    )
    fake_baseline = tmp_path / "baseline.txt"

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "CHAT_DIR", fake_chat_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)

    assert guard_module.main(["--update-baseline"]) == 0
    contents = fake_baseline.read_text(encoding="utf-8")
    assert "chat/runner.py:1:openminion.modules.session.todo:Todo" in contents
    assert "chat/runner.py:2:openminion.modules.foo:*" in contents
