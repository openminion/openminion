from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate/api_layout.py"
SPEC = importlib.util.spec_from_file_location("validate_api_layout", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_validate_api_layout_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip()
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True
    assert "server_app.py" not in payload["root_files"]


def test_validate_api_layout_allowlist_excludes_server_app() -> None:
    assert "server_app.py" not in MODULE.ALLOWED_ROOT_FILES


def test_validate_api_layout_rejects_unowned_root_file(tmp_path: Path) -> None:
    for package in MODULE.REQUIRED_SUBPACKAGES:
        package_root = tmp_path / package
        package_root.mkdir()
        (package_root / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "rogue_owner.py").write_text("", encoding="utf-8")

    _root_files, disallowed, missing = MODULE.validate_layout(tmp_path)

    assert disallowed == ["rogue_owner.py"]
    assert missing == []


def test_validate_api_layout_rejects_new_route_domain_import(tmp_path: Path) -> None:
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "probe.py").write_text(
        "from openminion.modules.memory.runtime.secret import owner\n",
        encoding="utf-8",
    )

    assert MODULE.collect_route_owner_imports(tmp_path) == {
        ("routes/probe.py", "openminion.modules.memory.runtime.secret")
    }


def test_validate_api_layout_rejects_complexity_increase() -> None:
    assert MODULE.compare_ratchet(
        {"max_callable_loc": 94}, {"max_callable_loc": 93}
    ) == ["API complexity increased: max_callable_loc 93 -> 94"]


def test_validate_api_layout_live_complexity_matches_baseline() -> None:
    assert (
        MODULE.compare_ratchet(
            MODULE.collect_complexity(MODULE.API_ROOT),
            MODULE._read_metrics(MODULE.COMPLEXITY_BASELINE),
        )
        == []
    )
