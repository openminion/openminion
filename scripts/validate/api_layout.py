"""Validate the public root layout of the `openminion.api` package."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "src" / "openminion" / "api"
ALLOWED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "agent.py",
    "config.py",
    "constants.py",
    "handoff.py",
    "metrics.py",
    "metrics_registry.py",
    "runtime.py",
    "turns.py",
}
REQUIRED_SUBPACKAGES = {"core", "queries", "responses", "routes", "server"}


def main() -> int:
    all_root_files = sorted(path.name for path in API_ROOT.iterdir() if path.is_file())
    disallowed = sorted(
        name for name in all_root_files if name not in ALLOWED_ROOT_FILES
    )
    subpackages = sorted(
        path.name
        for path in API_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    )
    missing_packages = sorted(REQUIRED_SUBPACKAGES.difference(subpackages))
    result = {
        "ok": not disallowed and not missing_packages,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "root_files": all_root_files,
        "disallowed_root_files": disallowed,
        "required_subpackages": sorted(REQUIRED_SUBPACKAGES),
        "missing_subpackages": missing_packages,
    }
    findings = []
    if disallowed:
        findings.append(f"Unexpected root files under api/: {', '.join(disallowed)}")
    if missing_packages:
        findings.append(
            f"Missing required api subpackages: {', '.join(missing_packages)}"
        )
    emit_json_report(
        "validate/api_layout.py",
        result,
        summary=(
            ("api root", API_ROOT),
            ("allowed root files", len(ALLOWED_ROOT_FILES)),
            ("required subpackages", len(REQUIRED_SUBPACKAGES)),
        ),
        findings=findings,
        ok_message="api root layout matches the public package contract.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
