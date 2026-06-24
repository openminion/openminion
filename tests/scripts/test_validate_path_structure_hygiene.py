from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate/path_structure_hygiene.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_path_structure_hygiene",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_validate_path_structure_hygiene_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip()
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True
    assert "_runtime" in payload["redundant_suffixes"]


def test_validate_source_tree_flags_deprecated_folder_name(tmp_path: Path) -> None:
    root = tmp_path / "src" / "openminion"
    legacy = root / "context" / "knowledge_graphs"
    legacy.mkdir(parents=True)
    (legacy / "__init__.py").write_text("", encoding="utf-8")

    findings = MODULE.validate_source_tree(root)

    assert findings == [
        "context/knowledge_graphs/: deprecated folder name 'knowledge_graphs'; use 'knowledge/'"
    ]


def test_validate_source_tree_flags_suffixes_and_parent_repetition(
    tmp_path: Path,
) -> None:
    root = tmp_path / "src" / "openminion"
    redundant = root / "storage" / "backends" / "backend_registry.py"
    redundant.parent.mkdir(parents=True)
    redundant.write_text("", encoding="utf-8")
    runtime_file = root / "services" / "agent" / "identity_runtime.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("", encoding="utf-8")

    findings = MODULE.validate_source_tree(root)

    assert findings == [
        "services/agent/identity_runtime.py: redundant suffix '_runtime'; use runtime.py inside a runtime/ owner or promote the runtime concern into a runtime/ folder",
        "storage/backends/backend_registry.py: filename repeats the parent owner; let the folder carry subsystem context",
    ]
