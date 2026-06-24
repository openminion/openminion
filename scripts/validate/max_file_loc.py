#!/usr/bin/env python3
"""Validate OpenMinion source files against the max-file-LOC baseline."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402


DEFAULT_CEILING = 1000
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_ROOT = REPO_ROOT / "src" / "openminion"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "baselines" / "max_file_loc_baseline.tsv"


@dataclass(frozen=True)
class BaselineEntry:
    path: str
    loc: int
    reason: str


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def count_loc(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError as exc:
        raise SystemExit(f"validate_max_file_loc: cannot read {path}: {exc}") from exc


def load_baseline(path: Path) -> dict[str, BaselineEntry]:
    entries: dict[str, BaselineEntry] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"validate_max_file_loc: cannot read baseline {path}: {exc}")
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise SystemExit(
                f"validate_max_file_loc: malformed baseline line {line_number}: "
                "expected path<TAB>loc<TAB>reason"
            )
        rel_path, raw_loc, reason = (part.strip() for part in parts)
        if not rel_path or not reason:
            raise SystemExit(
                f"validate_max_file_loc: malformed baseline line {line_number}: "
                "path and reason are required"
            )
        try:
            loc = int(raw_loc)
        except ValueError as exc:
            raise SystemExit(
                f"validate_max_file_loc: malformed baseline line {line_number}: "
                f"invalid loc {raw_loc!r}"
            ) from exc
        entries[rel_path] = BaselineEntry(path=rel_path, loc=loc, reason=reason)
    return entries


def source_files(source_root: Path) -> list[Path]:
    return sorted(path for path in source_root.rglob("*.py") if path.is_file())


def validate(
    *,
    repo_root: Path,
    source_root: Path,
    baseline_path: Path,
    ceiling: int,
) -> tuple[list[Finding], dict[str, int]]:
    baseline = load_baseline(baseline_path)
    seen: set[str] = set()
    findings: list[Finding] = []
    over_ceiling_count = 0
    checked_count = 0

    for path in source_files(source_root):
        checked_count += 1
        rel_path = path.relative_to(repo_root).as_posix()
        loc = count_loc(path)
        entry = baseline.get(rel_path)
        if loc > ceiling:
            over_ceiling_count += 1
            if entry is None:
                findings.append(
                    Finding(
                        code="new_over_ceiling_file",
                        path=rel_path,
                        detail=f"{loc} LOC exceeds ceiling {ceiling}",
                    )
                )
                continue
            seen.add(rel_path)
            if loc > entry.loc:
                findings.append(
                    Finding(
                        code="baselined_file_grew",
                        path=rel_path,
                        detail=f"{loc} LOC exceeds baseline {entry.loc}",
                    )
                )
            continue
        if entry is not None:
            seen.add(rel_path)
            findings.append(
                Finding(
                    code="stale_baseline_entry",
                    path=rel_path,
                    detail=f"{loc} LOC is at or below ceiling {ceiling}",
                )
            )

    for rel_path in sorted(set(baseline) - seen):
        findings.append(
            Finding(
                code="missing_baselined_file",
                path=rel_path,
                detail="baseline entry no longer exists under the source root",
            )
        )

    metrics = {
        "checked": checked_count,
        "ceiling": ceiling,
        "over_ceiling": over_ceiling_count,
        "baseline_entries": len(baseline),
    }
    return findings, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--ceiling", type=int, default=DEFAULT_CEILING)
    args = parser.parse_args(argv)

    findings, metrics = validate(
        repo_root=args.repo_root.resolve(),
        source_root=args.source_root.resolve(),
        baseline_path=args.baseline.resolve(),
        ceiling=max(args.ceiling, 1),
    )
    payload = {
        "validator": "validate_max_file_loc",
        "ok": not findings,
        "metrics": metrics,
        "findings": [
            {"code": finding.code, "path": finding.path, "detail": finding.detail}
            for finding in findings
        ],
    }
    emit_json_report(
        "validate_max_file_loc",
        payload,
        summary=(
            ("checked", metrics["checked"]),
            ("ceiling", metrics["ceiling"]),
            ("over ceiling", metrics["over_ceiling"]),
            ("baseline entries", metrics["baseline_entries"]),
        ),
        findings=[
            f"{finding.code}: {finding.path} — {finding.detail}" for finding in findings
        ],
        ok_message="max-file-LOC baseline is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
