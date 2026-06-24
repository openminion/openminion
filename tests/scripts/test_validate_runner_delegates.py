from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO_ROOT / "scripts" / "validate/runner_delegates.py"


def _load_guard_module():
    spec = importlib.util.spec_from_file_location(
        "validate_runner_delegates", GUARD_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_runner_delegates"] = module
    spec.loader.exec_module(module)
    return module


def _write_layout(
    tmp_path: Path, *, delegates: str, source: str = ""
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    delegates_path = repo / "src/openminion/modules/brain/runner/delegates.py"
    source_path = repo / "src/openminion/demo.py"
    delegates_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    delegates_path.write_text(delegates, encoding="utf-8")
    source_path.write_text(source, encoding="utf-8")
    return repo, delegates_path


def test_runner_delegate_validator_accepts_string_and_attribute_calls(tmp_path) -> None:
    guard = _load_guard_module()
    repo, delegates_path = _write_layout(
        tmp_path,
        delegates='RUNNER_DELEGATES: dict[str, object] = {"_used": object()}\n',
        source=(
            "def f(runner):\n"
            "    _runner_delegate('_used', runner)\n"
            "    runner._used()\n"
        ),
    )

    result = guard.validate(
        delegates_path=delegates_path,
        roots=(repo / "src/openminion",),
        repo_root=repo,
    )

    assert result["ok"] is True
    assert result["unused"] == []
    assert result["undefined"] == []


def test_runner_delegate_validator_accepts_reflective_consumers(tmp_path) -> None:
    guard = _load_guard_module()
    repo, delegates_path = _write_layout(
        tmp_path,
        delegates='RUNNER_DELEGATES: dict[str, object] = {"_reflected": object()}\n',
        source=(
            "from unittest.mock import patch\n"
            "def f(runner):\n"
            "    getattr(runner, '_reflected')\n"
            "    with patch.object(runner, '_reflected'):\n"
            "        pass\n"
        ),
    )

    result = guard.validate(
        delegates_path=delegates_path,
        roots=(repo / "src/openminion",),
        repo_root=repo,
    )

    assert result["ok"] is True
    assert result["unused"] == []


def test_runner_delegate_validator_flags_unused_delegate(tmp_path) -> None:
    guard = _load_guard_module()
    repo, delegates_path = _write_layout(
        tmp_path,
        delegates='RUNNER_DELEGATES: dict[str, object] = {"_unused": object()}\n',
        source="def f():\n    return None\n",
    )

    result = guard.validate(
        delegates_path=delegates_path,
        roots=(repo / "src/openminion",),
        repo_root=repo,
    )

    assert result["ok"] is False
    assert result["unused"] == ["_unused"]


def test_runner_delegate_validator_flags_undefined_string_call(tmp_path) -> None:
    guard = _load_guard_module()
    repo, delegates_path = _write_layout(
        tmp_path,
        delegates='RUNNER_DELEGATES: dict[str, object] = {"_known": object()}\n',
        source="def f(runner):\n    _runner_delegate('_missing', runner)\n",
    )

    result = guard.validate(
        delegates_path=delegates_path,
        roots=(repo / "src/openminion",),
        repo_root=repo,
    )

    assert result["ok"] is False
    assert result["undefined"] == ["_missing"]
    assert result["unused"] == ["_known"]


def test_runner_delegate_validator_flags_dynamic_string_call(tmp_path) -> None:
    guard = _load_guard_module()
    repo, delegates_path = _write_layout(
        tmp_path,
        delegates='RUNNER_DELEGATES: dict[str, object] = {"_known": object()}\n',
        source="def f(runner, name):\n    _runner_delegate(name, runner)\n",
    )

    result = guard.validate(
        delegates_path=delegates_path,
        roots=(repo / "src/openminion",),
        repo_root=repo,
    )

    assert result["ok"] is False
    assert result["dynamic_calls"][0]["kind"] == "dynamic"


def test_runner_delegate_validator_current_repo_is_clean() -> None:
    guard = _load_guard_module()

    result = guard.validate()

    assert result["ok"] is True
    assert result["metrics"]["delegate_keys"] == result["metrics"]["used_delegate_keys"]
