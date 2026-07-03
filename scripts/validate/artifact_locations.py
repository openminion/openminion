#!/usr/bin/env python3
"""Validate that generated scratch artifacts stay under workspace-tmp."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import sys

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.policy import load_quality_policy  # noqa: E402
from scripts.common.terminal_output import emit_plain_findings  # noqa: E402

DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def _policy() -> dict[str, object]:
    raw = load_quality_policy().get("artifact_locations", {})
    if not isinstance(raw, dict):
        raise SystemExit("artifact_locations policy must be an object")
    return raw


def _as_set(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw}


def _looks_like_scratch(name: str, *, tokens: set[str], suffixes: set[str]) -> bool:
    return Path(name).suffix in suffixes and any(token in name for token in tokens)


def _root_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_file())


def validate(workspace_root: Path) -> list[Finding]:
    policy = _policy()
    root_allowlist = _as_set(policy.get("workspace_root_allowlist"))
    package_allowlist = _as_set(policy.get("package_root_allowlist"))
    package_roots = _as_set(policy.get("package_roots"))
    tokens = _as_set(policy.get("scratch_name_tokens"))
    suffixes = _as_set(policy.get("scratch_suffixes"))

    findings: list[Finding] = []
    for path in _root_files(workspace_root):
        name = path.name
        if name in root_allowlist:
            continue
        if _looks_like_scratch(name, tokens=tokens, suffixes=suffixes):
            findings.append(
                Finding(
                    path=path.relative_to(workspace_root).as_posix(),
                    reason="scratch artifact belongs under workspace-tmp/<lane-or-purpose>/",
                )
            )

    for package in sorted(package_roots):
        package_root = workspace_root / package
        for path in _root_files(package_root):
            name = path.name
            if name in package_allowlist:
                continue
            if _looks_like_scratch(name, tokens=tokens, suffixes=suffixes):
                findings.append(
                    Finding(
                        path=path.relative_to(workspace_root).as_posix(),
                        reason="package-local scratch artifact belongs under umbrella workspace-tmp/",
                    )
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    args = parser.parse_args(argv)
    findings = validate(args.workspace_root.resolve())
    if findings:
        emit_plain_findings(
            "Generated scratch artifacts outside workspace-tmp:",
            [f"{finding.path}: {finding.reason}" for finding in findings],
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
