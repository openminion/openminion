#!/usr/bin/env python3
"""Validate broad exception handler counts against a ratchet baseline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402
from scripts.manual.report_broad_exception_handlers import scan  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "src" / "openminion"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "baselines" / "broad_exception_baseline.tsv"


@dataclass(frozen=True)
class BaselineEntry:
    path: str
    total: int
    silent_pass: int
    reason: str


def load_baseline(path: Path) -> dict[str, BaselineEntry]:
    entries: dict[str, BaselineEntry] = {}
    if not path.exists():
        return entries
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t", 3)
        if len(parts) != 4:
            raise SystemExit(
                f"broad-exception baseline line {line_number}: expected path<TAB>total<TAB>silent_pass<TAB>reason"
            )
        rel, raw_total, raw_silent, reason = (part.strip() for part in parts)
        try:
            total = int(raw_total)
            silent = int(raw_silent)
        except ValueError as exc:
            raise SystemExit(
                f"broad-exception baseline line {line_number}: invalid count"
            ) from exc
        entries[rel] = BaselineEntry(rel, total, silent, reason)
    return entries


def validate(*, root: Path, baseline_path: Path) -> tuple[list[str], dict[str, int]]:
    rows = scan(root)
    baseline = load_baseline(baseline_path)
    seen: set[str] = set()
    findings: list[str] = []
    for row in rows:
        row_path = Path(row.path)
        if row_path.is_absolute():
            try:
                rel = row_path.resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel = row_path.resolve().as_posix()
        else:
            rel = row.path
        entry = baseline.get(rel)
        if entry is None:
            findings.append(
                f"new_broad_exception_file: {rel} has {row.total} broad handlers"
            )
            continue
        seen.add(rel)
        if row.total > entry.total:
            findings.append(
                f"broad_exception_count_grew: {rel} has {row.total} > baseline {entry.total}"
            )
        if row.silent_pass > entry.silent_pass:
            findings.append(
                f"silent_pass_count_grew: {rel} has {row.silent_pass} > baseline {entry.silent_pass}"
            )
        if row.total == 0:
            findings.append(
                f"stale_broad_exception_baseline: {rel} now has 0 broad handlers"
            )
    for rel in sorted(set(baseline) - seen):
        findings.append(f"missing_broad_exception_baseline_file: {rel}")
    metrics = {
        "files_with_handlers": len(rows),
        "handler_count": sum(row.total for row in rows),
        "silent_pass_count": sum(row.silent_pass for row in rows),
        "baseline_entries": len(baseline),
    }
    return findings, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args(argv)
    findings, metrics = validate(root=args.root, baseline_path=args.baseline)
    payload = {
        "validator": "broad_exception",
        "ok": not findings,
        "metrics": metrics,
        "findings": findings,
    }
    emit_json_report(
        "broad_exception",
        payload,
        summary=(
            ("files", metrics["files_with_handlers"]),
            ("handlers", metrics["handler_count"]),
            ("silent pass", metrics["silent_pass_count"]),
            ("baseline entries", metrics["baseline_entries"]),
        ),
        findings=findings,
        ok_message="broad-exception baseline is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
