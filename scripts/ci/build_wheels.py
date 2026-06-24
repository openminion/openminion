#!/usr/bin/env python3
"""Build wheel artifacts for selected modules."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common.ci_support import load_json_list  # noqa: E402
from common.repo_modules import module_pyproject_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CI_ARTIFACTS_ROOT = ROOT / ".openminion" / "runtime" / "ci"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build wheel artifacts for modules.")
    parser.add_argument("--modules-json", required=True)
    parser.add_argument("--out-dir", default=str(CI_ARTIFACTS_ROOT / "wheels"))
    parser.add_argument(
        "--manifest",
        default=str(CI_ARTIFACTS_ROOT / "wheels" / "manifest.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    modules = load_json_list(args.modules_json)
    if not modules:
        print("No modules provided; skipping wheel build.")
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "bundle_version": "dev",
        "modules": [],
    }

    for module in modules:
        module_path = ROOT / module
        if not module_pyproject_path(ROOT, module).exists():
            print(f"Skipping {module}: missing pyproject.toml")
            continue

        module_out = out_dir / module
        module_out.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(module_out),
            str(module_path),
        ]
        print("Running:", " ".join(cmd))
        proc = subprocess.run(cmd, cwd=ROOT)
        if proc.returncode != 0:
            return proc.returncode

        wheels = sorted(path.name for path in module_out.glob("*.whl"))
        manifest["modules"].append({"module": module, "wheels": wheels})

    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"Wrote wheel manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
