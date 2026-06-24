from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate/openminion_root_layout.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_openminion_root_layout", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_clean_root(root: Path) -> None:
    for dirname in MODULE.ALLOWED_TOP_LEVEL_DIRS:
        (root / dirname).mkdir(parents=True)
    for filename in MODULE.ALLOWED_ROOT_FILES:
        (root / filename).write_text("", encoding="utf-8")


def test_validate_openminion_root_layout_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip()
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True
    assert "eval" not in payload["allowed_top_level_dirs"]
    assert "memory" not in payload["allowed_top_level_dirs"]


def test_validate_root_layout_rejects_unexpected_root_dir(tmp_path: Path) -> None:
    _write_clean_root(tmp_path)
    (tmp_path / "eval").mkdir()

    errors = MODULE.validate_root_layout(tmp_path)

    assert errors == ["Unexpected top-level src/openminion dirs: eval"]


def test_validate_root_layout_rejects_unexpected_root_file(tmp_path: Path) -> None:
    _write_clean_root(tmp_path)
    (tmp_path / "compat.py").write_text("", encoding="utf-8")

    errors = MODULE.validate_root_layout(tmp_path)

    assert errors == ["Unexpected files under src/openminion/: compat.py"]
