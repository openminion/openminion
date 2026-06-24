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
