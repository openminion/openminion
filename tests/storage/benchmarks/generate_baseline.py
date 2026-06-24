from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_benchmark_module():
    module_path = Path(__file__).with_name("test_benchmarks.py")
    spec = importlib.util.spec_from_file_location(
        "openminion_storage_benchmarks",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load benchmark module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = _load_benchmark_module()
    results = module.generate_baseline(module.BASELINE_PATH)
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"\nWrote benchmark baseline to {module.BASELINE_PATH}")
    return 0
