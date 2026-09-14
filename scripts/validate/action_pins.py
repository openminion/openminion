#!/usr/bin/env python3
"""Require immutable revisions for external GitHub Actions."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ACTION_PATTERN = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
DOCKER_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class UnpinnedAction:
    path: Path
    line: int
    reference: str


def _workflow_files(repo_root: Path) -> list[Path]:
    patterns = (
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        ".github/actions/**/*.yml",
        ".github/actions/**/*.yaml",
    )
    return sorted({path for pattern in patterns for path in repo_root.glob(pattern)})


def find_unpinned_actions(repo_root: Path) -> list[UnpinnedAction]:
    findings: list[UnpinnedAction] = []
    for path in _workflow_files(repo_root):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = ACTION_PATTERN.match(line)
            if match is None:
                continue
            reference = match.group(1).strip("'\"")
            if reference.startswith("./"):
                continue
            _, separator, revision = reference.rpartition("@")
            revision_pattern = (
                DOCKER_DIGEST_PATTERN
                if reference.startswith("docker://")
                else COMMIT_PATTERN
            )
            if not separator or revision_pattern.fullmatch(revision) is None:
                findings.append(
                    UnpinnedAction(
                        path=path.relative_to(repo_root),
                        line=line_number,
                        reference=reference,
                    )
                )
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require external GitHub Actions to use full commit SHAs."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    findings = find_unpinned_actions(args.root.resolve())
    for finding in findings:
        print(f"{finding.path}:{finding.line}: mutable action ref {finding.reference}")
    if findings:
        print(f"workflow action pins: {len(findings)} finding(s)")
        return 1
    print("workflow action pins: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
