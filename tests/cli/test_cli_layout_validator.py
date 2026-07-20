from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate/cli_layout.py"
    )
    spec = importlib.util.spec_from_file_location("validate_cli_layout", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_validator_module()


def test_validate_cli_layout_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_validate_root_layout_flags_legacy_flat_file(tmp_path: Path) -> None:
    for file_name in MODULE.ALLOWED_ROOT_FILES:
        (tmp_path / file_name).write_text("", encoding="utf-8")
    for dirname, expected_files in MODULE.GROUPED_LAYOUT.items():
        path = tmp_path / dirname
        path.mkdir()
        (path / "__init__.py").write_text("", encoding="utf-8")
        for file_name in expected_files:
            (path / file_name).write_text("", encoding="utf-8")
    (tmp_path / "parser.py").write_text("# legacy\n", encoding="utf-8")

    errors = MODULE.validate_root_layout(tmp_path)

    assert errors
    assert any("parser.py" in error for error in errors)


def test_current_doc_scan_rejects_retired_cli_guidance(tmp_path: Path) -> None:
    path = tmp_path / "README.md"
    path.write_text(
        "Use the hidden compatibility alias and OPENMINION_FOCUS_BACKEND.\n",
        encoding="utf-8",
    )

    errors = MODULE.scan_current_doc(path)

    assert errors == [
        f"{path}:1: removed focus backend env",
        f"{path}:1: hidden alias claim",
    ]


def test_current_doc_scan_allows_explicit_retirement_language(tmp_path: Path) -> None:
    path = tmp_path / "README.md"
    path.write_text(
        "Legacy `openminion chat` is retired and rejected.\n",
        encoding="utf-8",
    )

    assert MODULE.scan_current_doc(path) == []
