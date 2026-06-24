from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT / "scripts" / "manual" / "audit_characterization_snapshot_brittleness.py"
)


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_characterization_snapshot_brittleness", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_audit_returns_structured_report() -> None:
    audit = _load_audit().audit()
    assert "characterization_files_scanned" in audit
    assert "files_with_findings" in audit
    assert "totals" in audit
    assert "files" in audit


def test_audit_finds_some_characterization_files() -> None:
    audit = _load_audit().audit()
    # Sanity: we must be scanning at least 5 files (the repo has 30+).
    assert audit["characterization_files_scanned"] >= 5


def test_audit_totals_match_per_file_counts() -> None:
    audit = _load_audit().audit()
    totals = audit["totals"]
    for key in (
        "long_string_eq_compare",
        "large_collection_eq_compare",
        "module_all_eq_compare",
        "snapshot_keyword",
    ):
        per_file_sum = sum(f[key] for f in audit["files"])
        assert totals[key] == per_file_sum, f"totals[{key}] disagree with per-file sum"


def test_audit_findings_files_are_sorted() -> None:
    audit = _load_audit().audit()
    paths = [f["path"] for f in audit["files"]]
    assert paths == sorted(paths), "audit findings must be deterministic-sorted"


def test_audit_excludes_its_own_script_tests() -> None:
    audit = _load_audit().audit()
    paths = [f["path"] for f in audit["files"]]
    assert (
        "tests/scripts/test_audit_characterization_snapshot_brittleness.py" not in paths
    )


def test_audit_main_returns_zero_informational() -> None:
    audit_mod = _load_audit()
    exit_code = audit_mod.main([])
    assert exit_code == 0
