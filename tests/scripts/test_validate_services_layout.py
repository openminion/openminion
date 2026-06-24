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
