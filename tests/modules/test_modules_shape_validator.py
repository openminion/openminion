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
validate_test_owner = _validator.validate_test_owner


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


def test_validate_test_owner_accepts_nonempty_test_file(tmp_path: Path) -> None:
    owner = tmp_path / "demo"
    owner.mkdir()
    (owner / "test_demo.py").write_text("def test_demo(): pass\n", encoding="utf-8")

    assert validate_test_owner("demo", tmp_path) == []


def test_validate_test_owner_reports_missing_owner(tmp_path: Path) -> None:
    errors = validate_test_owner("demo", tmp_path)

    assert errors == [
        f"module 'demo' has no non-empty test owner under {tmp_path / 'demo'}"
    ]


def test_validate_test_owner_rejects_empty_test_file(tmp_path: Path) -> None:
    owner = tmp_path / "demo"
    owner.mkdir()
    (owner / "test_demo.py").touch()

    assert validate_test_owner("demo", tmp_path)


def test_validate_test_owner_rejects_non_test_content(tmp_path: Path) -> None:
    owner = tmp_path / "demo"
    owner.mkdir()
    (owner / "README.md").write_text("# Demo\n", encoding="utf-8")

    assert validate_test_owner("demo", tmp_path)


def test_validate_test_owner_accepts_prompting_override(tmp_path: Path) -> None:
    owner = tmp_path / "services" / "prompting"
    owner.mkdir(parents=True)
    (owner / "test_prompting_contracts.py").write_text(
        "def test_prompting(): pass\n",
        encoding="utf-8",
    )

    assert validate_test_owner("prompting", tmp_path) == []
