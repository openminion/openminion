from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate/no_source_e2e_artifact_refs.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_no_source_e2e_artifact_refs",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_validate_no_source_e2e_artifact_refs_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip()
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_validate_source_e2e_artifact_refs_rejects_legacy_artifact_path(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "module.py"
    source_file.write_text(
        'EVIDENCE = "artifacts/cli-chat-e2e/example.json"\n',
        encoding="utf-8",
    )

    assert MODULE.validate_source_e2e_artifact_refs(tmp_path) == [
        "module.py:1: artifacts/cli-chat-e2e"
    ]


def test_validate_source_e2e_artifact_refs_allows_runtime_artifact_api(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "module.py"
    source_file.write_text(
        'path = "artifacts/exec/session-stdout.log"\n',
        encoding="utf-8",
    )

    assert MODULE.validate_source_e2e_artifact_refs(tmp_path) == []
