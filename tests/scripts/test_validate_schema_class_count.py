from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate/schema_classes.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_schema_class_count", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"cannot load schema class-count validator from {VALIDATOR_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_schema_class_count"] = module
    spec.loader.exec_module(module)
    return module


def _write_schema(path: Path, class_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"class C{index}:\n    pass" for index in range(class_count))
    path.write_text(body + "\n")


def _write_baseline(path: Path, *entries: tuple[str, int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# path\tclass_count\treason"]
    lines.extend(f"{entry[0]}\t{entry[1]}\t{entry[2]}" for entry in entries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_validator_accepts_baselined_over_ceiling_schema(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    file_path = source / "schemas.py"
    baseline = repo / "baseline.tsv"
    _write_schema(file_path, 3)
    _write_baseline(baseline, ("src/openminion/schemas.py", 3, "test"))

    findings, metrics = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert findings == []
    assert metrics["at_or_over_ceiling"] == 1


def test_validator_rejects_new_over_ceiling_schema(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    _write_schema(source / "schemas.py", 3)
    baseline = repo / "baseline.tsv"
    _write_baseline(baseline)

    findings, _ = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert [finding.code for finding in findings] == ["new_over_ceiling_schema"]


def test_validator_rejects_baselined_schema_growth(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    _write_schema(source / "schemas.py", 4)
    baseline = repo / "baseline.tsv"
    _write_baseline(baseline, ("src/openminion/schemas.py", 3, "test"))

    findings, _ = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert [finding.code for finding in findings] == ["baselined_schema_grew"]


def test_validator_rejects_stale_schema_baseline_entry(tmp_path: Path) -> None:
    validator = _load_validator()
    repo = tmp_path
    source = repo / "src" / "openminion"
    _write_schema(source / "schemas.py", 2)
    baseline = repo / "baseline.tsv"
    _write_baseline(baseline, ("src/openminion/schemas.py", 3, "test"))

    findings, _ = validator.validate(
        repo_root=repo,
        source_root=source,
        baseline_path=baseline,
        ceiling=3,
    )

    assert [finding.code for finding in findings] == ["stale_baseline_entry"]
