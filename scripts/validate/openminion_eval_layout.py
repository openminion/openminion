#!/usr/bin/env python3
"""Guard `openminion-eval` against root-layout drift."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ROOT_FILES = {
    "README.md",
    "__main__.py",
    "__init__.py",
    "boundary_artifacts.py",
    "cli.py",
    "config.py",
    "constants.py",
    "datasets.py",
    "family_registry.py",
    "family_support.py",
    "integration_quarantine.py",
    "interfaces.py",
    "manual.py",
    "paths.py",
    "py.typed",
    "runner.py",
    "schemas.py",
    "scorer.py",
    "suite.py",
    "suite_artifacts.py",
    "suite_selection.py",
}
ALLOWED_TOP_LEVEL_DIRS = {
    "cases",
    "closure",
    "freshness",
    "goal_trajectory",
    "memory_effectiveness",
    "policy",
    "reporting",
    "routing",
    "skills",
    "tools",
}
REQUIRED_TOP_LEVEL_DIRS = ALLOWED_TOP_LEVEL_DIRS - {"memory_effectiveness"}


def _resolve_eval_root() -> Path:
    explicit_root = os.environ.get("OPENMINION_EVAL_ROOT", "").strip()
    candidate_roots: list[Path] = []
    if explicit_root:
        candidate_roots.append(Path(explicit_root).expanduser())
    candidate_roots.extend(
        [
            REPO_ROOT / ".deps" / "openminion-eval",
            REPO_ROOT.parent / "openminion-eval",
        ]
    )

    for repo_root in candidate_roots:
        package_root = repo_root / "src" / "openminion_eval"
        if package_root.exists():
            return package_root

    fallback_repo_root = candidate_roots[0] if candidate_roots else REPO_ROOT
    return fallback_repo_root / "src" / "openminion_eval"


EVAL_ROOT = _resolve_eval_root()


def validate_root_layout(root: Path = EVAL_ROOT) -> list[str]:
    errors: list[str] = []
    if not root.exists():
        errors.append(f"openminion-eval package root missing at {root}")
        return errors

    root_files = sorted(path.name for path in root.iterdir() if path.is_file())
    unexpected_root_files = [
        name for name in root_files if name not in ALLOWED_ROOT_FILES
    ]
    if unexpected_root_files:
        errors.append(
            "Unexpected root files under openminion-eval/src/openminion_eval/: "
            + ", ".join(unexpected_root_files)
        )

    top_level_dirs = sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    )
    unexpected_dirs = [
        name for name in top_level_dirs if name not in ALLOWED_TOP_LEVEL_DIRS
    ]
    if unexpected_dirs:
        errors.append(
            "Unexpected top-level openminion-eval dirs: " + ", ".join(unexpected_dirs)
        )

    missing_dirs = sorted(REQUIRED_TOP_LEVEL_DIRS.difference(top_level_dirs))
    if missing_dirs:
        errors.append(
            "Missing admitted openminion-eval subpackages: " + ", ".join(missing_dirs)
        )

    if not (root / "README.md").exists():
        errors.append("openminion-eval/src/openminion_eval/README.md missing")
    return errors


def main() -> int:
    errors = validate_root_layout()
    result = {
        "ok": not errors,
        "allowed_root_files": sorted(ALLOWED_ROOT_FILES),
        "admitted_subpackages": sorted(ALLOWED_TOP_LEVEL_DIRS),
        "required_subpackages": sorted(REQUIRED_TOP_LEVEL_DIRS),
    }
    emit_json_report(
        "validate_openminion_eval_layout",
        result,
        summary=(
            ("package root", EVAL_ROOT),
            ("allowed root files", len(ALLOWED_ROOT_FILES)),
            ("admitted subpackages", len(ALLOWED_TOP_LEVEL_DIRS)),
            ("required subpackages", len(REQUIRED_TOP_LEVEL_DIRS)),
        ),
        findings=errors,
        ok_message="openminion-eval layout is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
