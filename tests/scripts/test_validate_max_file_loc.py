from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate/max_file_loc.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_max_file_loc", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load max-file-LOC validator from {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_max_file_loc"] = module
    spec.loader.exec_module(module)
    return module


def _write_source(path: Path, line_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join("pass" for _ in range(line_count)) + "\n")


def _write_baseline(path: Path, *entries: tuple[str, int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# path\tloc\treason"]
    lines.extend(f"{entry[0]}\t{entry[1]}\t{entry[2]}" for entry in entries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_validator_accepts_baselined_over_ceiling_file(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    file_path = source / "large.py"
    baseline = repo / "baseline.tsv"
    _write_source(file_path, 4)
    _write_baseline(baseline, ("src/openminion/large.py", 4, "test baseline"))

    findings, metrics = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert findings == []
    assert metrics["over_ceiling"] == 1


def test_validator_rejects_new_over_ceiling_file(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    _write_source(source / "large.py", 4)
    baseline = repo / "baseline.tsv"
    _write_baseline(baseline)

    findings, _ = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert [finding.code for finding in findings] == ["new_over_ceiling_file"]


def test_validator_rejects_baselined_growth(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    _write_source(source / "large.py", 5)
    baseline = repo / "baseline.tsv"
    _write_baseline(baseline, ("src/openminion/large.py", 4, "test baseline"))

    findings, _ = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert [finding.code for finding in findings] == ["baselined_file_grew"]


def test_validator_rejects_stale_baseline_entry(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    _write_source(source / "small.py", 2)
    baseline = repo / "baseline.tsv"
    _write_baseline(baseline, ("src/openminion/small.py", 4, "test baseline"))

    findings, _ = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert [finding.code for finding in findings] == ["stale_baseline_entry"]
