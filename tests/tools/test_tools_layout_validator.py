from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate/tools_layout.py"
    )
    spec = importlib.util.spec_from_file_location("validate_tools_layout", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_validator_module()


def test_validate_tools_layout_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_validate_root_layout_flags_legacy_variant_dir(tmp_path: Path) -> None:
    for file_name in (
        "README.md",
        "__init__.py",
        "__main__.py",
        "config.py",
        "constants.py",
        "env.py",
    ):
        (tmp_path / file_name).write_text("", encoding="utf-8")
    for dirname in MODULE.ALLOWED_TOP_LEVEL_DIRS:
        path = tmp_path / dirname
        path.mkdir()
        (path / "__init__.py").write_text("", encoding="utf-8")
    for category in MODULE.MULTI_PROVIDER_LAYOUT:
        providers_root = tmp_path / category / "providers"
        providers_root.mkdir(parents=True, exist_ok=True)
        (providers_root / "__init__.py").write_text("", encoding="utf-8")
        for provider in MODULE.MULTI_PROVIDER_LAYOUT[category]:
            (providers_root / provider).mkdir()
    (tmp_path / "fetch" / "providers" / "core_http.py").write_text("", encoding="utf-8")
    (tmp_path / "search_brave").mkdir()

    errors = MODULE.validate_root_layout(tmp_path)

    assert errors
    assert any("search_brave" in error for error in errors)
