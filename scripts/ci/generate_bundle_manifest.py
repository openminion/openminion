#!/usr/bin/env python3
"""Generate bundle manifest for release artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.repo_modules import (  # noqa: E402
    discover_repo_modules,
    read_project_version,
)

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "ci" / "module_catalog.json"
CI_ARTIFACTS_ROOT = ROOT / ".openminion" / "runtime" / "ci"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate OpenMinion bundle manifest.")
    parser.add_argument("--version", default="dev")
    parser.add_argument("--min-data-version", default="1")
    parser.add_argument("--max-data-version", default="1")
    parser.add_argument(
        "--output",
        default=str(CI_ARTIFACTS_ROOT / "bundle" / "bundle-manifest.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if CATALOG.exists():
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        preferred_modules = list(catalog.get("core_modules", []))
    else:
        preferred_modules = []

    modules = discover_repo_modules(ROOT)

    ordered: list[str] = []
    seen: set[str] = set()
    for name in preferred_modules + modules:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)

    payload = {
        "bundle_version": str(args.version),
        "min_data_version": str(args.min_data_version),
        "max_data_version": str(args.max_data_version),
        "modules": [
            {"name": name, "version": read_project_version(ROOT, name)}
            for name in ordered
        ],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote bundle manifest to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
