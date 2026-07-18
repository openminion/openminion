from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate/pydantic_extra_allow_audit.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "validate_pydantic_extra_allow_audit", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit_mod():
    return _load_audit_module()


def test_live_tree_audit_is_clean(audit_mod) -> None:
    report = audit_mod.audit()
    assert report.ok, f"audit drift: {report.findings}"
    # Sanity guards: the audit found at least the original 22-26 known
    # declarations, and the allowlist contains a row for each.
    assert len(report.decls) >= 22
    assert len(report.allowlist) == len(report.decls)


def _write_fixture(tmp_path: Path, py_body: str, allowlist_body: str):
    src_root = tmp_path / "src"
    pkg = src_root / "openminion_fixture"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "models.py").write_text(py_body, encoding="utf-8")
    allowlist = tmp_path / "allowlist.tsv"
    allowlist.write_text(allowlist_body, encoding="utf-8")
    return src_root, allowlist


def test_unjustified_extra_allow_is_flagged(audit_mod, tmp_path) -> None:
    py = (
        "from pydantic import BaseModel, ConfigDict\n\n"
        "class NewLazyModel(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    x: int = 0\n"
    )
    src_root, allowlist = _write_fixture(tmp_path, py, "# empty\n")
    src_root = src_root / "openminion_fixture"
    monkey_root = audit_mod.REPO_ROOT
    audit_mod.REPO_ROOT = tmp_path
    try:
        report = audit_mod.audit(src_root=src_root, allowlist_path=allowlist)
    finally:
        audit_mod.REPO_ROOT = monkey_root
    codes = {f["code"] for f in report.findings}
    assert "unjustified_extra_allow" in codes
    assert not report.ok


def test_stale_allowlist_row_is_flagged(audit_mod, tmp_path) -> None:
    py = (
        "from pydantic import BaseModel\n\n"
        "class NoLongerAllow(BaseModel):\n"
        "    x: int = 0\n"
    )
    rel_path = "src/openminion_fixture/models.py"
    allowlist_body = f"{rel_path}\t999\tGhostModel\tstale-test\n"
    src_root, allowlist = _write_fixture(tmp_path, py, allowlist_body)
    src_root = src_root / "openminion_fixture"
    # Patch REPO_ROOT for the allowlist's relative-path interpretation.
    monkey_root = audit_mod.REPO_ROOT
    audit_mod.REPO_ROOT = tmp_path
    try:
        report = audit_mod.audit(src_root=src_root, allowlist_path=allowlist)
    finally:
        audit_mod.REPO_ROOT = monkey_root
    codes = {f["code"] for f in report.findings}
    assert "stale_allowlist_row" in codes
    assert not report.ok


def test_model_name_mismatch_is_flagged(audit_mod, tmp_path) -> None:
    py = (
        "from pydantic import BaseModel, ConfigDict\n\n"
        "class RealName(BaseModel):\n"
        '    model_config = ConfigDict(extra="allow")\n'
        "    x: int = 0\n"
    )
    rel_path = "src/openminion_fixture/models.py"
    # ConfigDict is on line 4 of the fixture.
    line_no = 4
    allowlist_body = f"{rel_path}\t{line_no}\tWrongName\tmismatch-test\n"
    src_root, allowlist = _write_fixture(tmp_path, py, allowlist_body)
    src_root = src_root / "openminion_fixture"
    monkey_root = audit_mod.REPO_ROOT
    audit_mod.REPO_ROOT = tmp_path
    try:
        report = audit_mod.audit(src_root=src_root, allowlist_path=allowlist)
    finally:
        audit_mod.REPO_ROOT = monkey_root
    codes = {f["code"] for f in report.findings}
    assert "model_name_mismatch" in codes


def test_extra_forbid_pattern_rejects_unknown_field() -> None:
    from pydantic import BaseModel, ConfigDict, ValidationError

    class _StrictDemo(BaseModel):
        model_config = ConfigDict(extra="forbid")
        x: int = 0

    instance = _StrictDemo.model_validate({"x": 7})
    assert instance.x == 7

    with pytest.raises(ValidationError):
        _StrictDemo.model_validate({"x": 7, "rogue": "bad"})
