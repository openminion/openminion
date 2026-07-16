from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate/openminion_eval_layout.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_openminion_eval_layout", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_clean_root(root: Path) -> None:
    for dirname in MODULE.REQUIRED_TOP_LEVEL_DIRS:
        (root / dirname).mkdir(parents=True)
    for filename in MODULE.ALLOWED_ROOT_FILES:
        (root / filename).write_text("", encoding="utf-8")


def test_live_eval_layout_is_admitted() -> None:
    assert MODULE.validate_root_layout() == []
    assert "boundary_artifacts.py" in MODULE.ALLOWED_ROOT_FILES
    assert "suite_selection.py" in MODULE.ALLOWED_ROOT_FILES


def test_eval_layout_rejects_unexpected_root_file(tmp_path: Path) -> None:
    _write_clean_root(tmp_path)
    (tmp_path / "extra_owner.py").write_text("", encoding="utf-8")

    assert MODULE.validate_root_layout(tmp_path) == [
        "Unexpected root files under openminion-eval/src/openminion_eval/: "
        "extra_owner.py"
    ]
