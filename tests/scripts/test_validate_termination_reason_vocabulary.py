from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate/termination_reason_vocabulary.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_termination_reason_vocabulary", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_live_tree_passes_vocabulary_audit() -> None:
    mod = _load_validator()
    report = mod.audit()
    assert report["ok"], f"vocabulary drift: {report['findings']}"
    assert report["vocabulary_size"] >= 20
    assert report["emit_sites_scanned"] >= 10


def test_vocabulary_contains_ar12_completion_signal() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "task_complete" in vocab


def test_vocabulary_contains_ar12_loop_no_progress() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "loop_no_progress" in vocab


def test_vocabulary_contains_ar12_context_exhausted() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "context_exhausted" in vocab


def test_vocabulary_contains_ar12_budget_exhausted_with_partial() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "budget_exhausted_with_partial_result" in vocab


def test_vocabulary_contains_ar12_awaiting_user_decision() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "awaiting_user_decision" in vocab


def test_vocabulary_contains_ar15_time_budget_exceeded() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "time_budget_exceeded" in vocab


def test_vocabulary_contains_ar09_repeated_failure_stalled() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "repeated_failure_stalled" in vocab


def test_vocabulary_contains_b09_empty_provider_response() -> None:
    mod = _load_validator()
    vocab = mod._load_vocabulary()
    assert "empty_provider_response" in vocab


def test_drift_case_surfaces_unknown_value(tmp_path: Path, monkeypatch) -> None:
    mod = _load_validator()
    pkg = tmp_path / "fixture_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "emit_site.py").write_text(
        "def foo():\n    return build_outcome(termination_reason='rogue_value')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "SCAN_ROOTS", [pkg])
    report = mod.audit()
    assert not report["ok"]
    codes = {f["value"] for f in report["findings"]}
    assert "rogue_value" in codes


def test_metadata_dict_form_also_audited(tmp_path: Path, monkeypatch) -> None:
    mod = _load_validator()
    pkg = tmp_path / "fixture_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "emit_site.py").write_text(
        "def foo():\n    return {'tool_loop_termination_reason': 'rogue_metadata'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "SCAN_ROOTS", [pkg])
    report = mod.audit()
    assert not report["ok"]
    assert any(f["value"] == "rogue_metadata" for f in report["findings"])


def test_non_vocabulary_constants_file_is_still_audited(
    tmp_path: Path, monkeypatch
) -> None:
    mod = _load_validator()
    pkg = tmp_path / "fixture_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "constants.py").write_text(
        "def foo():\n    return build_outcome(termination_reason='rogue_constant')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "SCAN_ROOTS", [pkg])
    report = mod.audit()
    assert not report["ok"]
    assert any(f["value"] == "rogue_constant" for f in report["findings"])
