from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate/base_charter.py"
    )
    spec = importlib.util.spec_from_file_location("validate_base_charter", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_validator_module()


def test_validate_base_charter_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_validate_root_layout_flags_unexpected_subpackage(tmp_path: Path) -> None:
    for file_name in MODULE.ALLOWED_ROOT_FILES:
        (tmp_path / file_name).write_text("", encoding="utf-8")
    for dirname in MODULE.ALLOWED_TOP_LEVEL_DIRS:
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "telemetry").mkdir()

    errors = MODULE.validate_root_layout(tmp_path)

    assert errors
    assert any("telemetry" in error for error in errors)
