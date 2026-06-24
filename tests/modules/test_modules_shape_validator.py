from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate/modules_shape.py"
    )
    spec = importlib.util.spec_from_file_location("validate_modules_shape", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_validator = _load_validator_module()
validate_root_files = _validator.validate_root_files
validate_subsystem = _validator.validate_subsystem


def test_validate_root_files_flags_unexpected_file(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Modules\n", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "config.py").write_text("", encoding="utf-8")
    (tmp_path / "surprise.py").write_text("", encoding="utf-8")

    errors = validate_root_files(tmp_path)

    assert errors
    assert "surprise.py" in errors[0]


def test_validate_subsystem_accepts_documented_shape(tmp_path: Path) -> None:
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "README.md").write_text(
        "# Demo Module\n\nShape: `small-primitive`\n",
        encoding="utf-8",
    )
    (pkg / "interfaces.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert validate_subsystem(pkg) == []


def test_validate_subsystem_requires_readme_and_marker(tmp_path: Path) -> None:
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "misc.py").write_text("VALUE = 1\n", encoding="utf-8")

    errors = validate_subsystem(pkg)

    assert errors
    assert "missing README.md charter" in errors[0]
