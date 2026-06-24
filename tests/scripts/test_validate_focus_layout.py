from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO_ROOT / "scripts" / "validate" / "focus_layout.py"


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("validate_focus_layout", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_focus_layout"] = module
    spec.loader.exec_module(module)
    return module


def test_focus_layout_runs_all_registered_checks(monkeypatch) -> None:
    guard = _load_guard_module()
    calls: list[str] = []

    def first(argv: list[str]) -> int:
        calls.append(f"first:{argv!r}")
        return 0

    def second(argv: list[str]) -> int:
        calls.append(f"second:{argv!r}")
        return 0

    monkeypatch.setattr(
        guard,
        "CHECKS",
        (("first", first), ("second", second)),
    )

    assert guard.main([]) == 0
    assert calls == ["first:[]", "second:[]"]


def test_focus_layout_fails_if_any_registered_check_fails(monkeypatch) -> None:
    guard = _load_guard_module()

    def ok(argv: list[str]) -> int:
        return 0

    def fail(argv: list[str]) -> int:
        return 1

    monkeypatch.setattr(guard, "CHECKS", (("ok", ok), ("fail", fail)))

    assert guard.main([]) == 1


def test_focus_layout_rejects_arguments() -> None:
    guard = _load_guard_module()

    assert guard.main(["--update-baseline"]) == 2
