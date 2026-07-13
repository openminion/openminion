from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate/services_layout.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_scan_text_file_treats_missing_path_as_empty(monkeypatch) -> None:
    validator = _load_module("validate_services_layout_test", VALIDATOR_PATH)
    missing = REPO_ROOT / "src" / "openminion" / "__pycache__" / "transient.pyc.123"

    def _raise_missing(self: Path, encoding: str = "utf-8") -> str:
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "read_text", _raise_missing)

    assert validator._scan_text_file(missing) == []


def _write_baseline(path: Path, *, files: int = 20, loc: int = 200) -> None:
    path.write_text(
        f"scope\tmax_python_files\tmax_loc\nservices\t{files}\t{loc}\n",
        encoding="utf-8",
    )


def test_validator_rejects_retired_import_path(tmp_path: Path) -> None:
    validator = _load_module("validate_services_layout_retired", VALIDATOR_PATH)
    probe = tmp_path / "probe.py"
    retired = "openminion.services." + "integration.skill_harness"
    probe.write_text(f"from {retired} import run_skill_harness\n", encoding="utf-8")

    hits = validator._scan_text_file(probe)

    assert len(hits) == 1
    assert "legacy import/string" in hits[0]


def test_validator_rejects_retired_physical_module(tmp_path: Path) -> None:
    validator = _load_module("validate_services_layout_physical", VALIDATOR_PATH)
    services_root = tmp_path / "services"
    retired = services_root / "agent" / "identity.py"
    retired.parent.mkdir(parents=True)
    retired.write_text("VALUE = 1\n", encoding="utf-8")
    baseline = tmp_path / "baseline.tsv"
    _write_baseline(baseline)

    hits = validator.validate_layout(
        services_root=services_root,
        scan_roots=[],
        baseline_path=baseline,
    )

    assert "Retired service module exists: agent/identity.py" in hits


def test_validator_rejects_second_owner_stack(tmp_path: Path) -> None:
    validator = _load_module("validate_services_layout_stack", VALIDATOR_PATH)
    services_root = tmp_path / "services"
    duplicate = services_root / "security" / "engine.py"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text("VALUE = 1\n", encoding="utf-8")
    baseline = tmp_path / "baseline.tsv"
    _write_baseline(baseline)

    hits = validator.validate_layout(
        services_root=services_root,
        scan_roots=[],
        baseline_path=baseline,
    )

    assert "Duplicate security owner stack files: engine.py" in hits


def test_validator_rejects_file_and_loc_ratchet_increases(tmp_path: Path) -> None:
    validator = _load_module("validate_services_layout_budget", VALIDATOR_PATH)
    services_root = tmp_path / "services"
    services_root.mkdir()
    (services_root / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    baseline = tmp_path / "baseline.tsv"
    _write_baseline(baseline, files=0, loc=0)

    hits = validator.validate_layout(
        services_root=services_root,
        scan_roots=[],
        baseline_path=baseline,
    )

    assert "services Python file ratchet increased: 1 > 0" in hits
    assert "services LOC ratchet increased: 1 > 0" in hits


def test_validator_accepts_canonical_owner_stack(tmp_path: Path) -> None:
    validator = _load_module("validate_services_layout_canonical", VALIDATOR_PATH)
    services_root = tmp_path / "services"
    canonical = services_root / "stats" / "__init__.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("VALUE = 1\n", encoding="utf-8")
    baseline = tmp_path / "baseline.tsv"
    _write_baseline(baseline)

    assert (
        validator.validate_layout(
            services_root=services_root,
            scan_roots=[],
            baseline_path=baseline,
        )
        == []
    )
