#!/usr/bin/env python3
"""Warn on new Python filenames with heavy underscore chaining."""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
from collections.abc import Iterable, Sequence

_SCRIPT_ROOT = pathlib.Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

REPO_IMPORT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

_terminal_output = importlib.import_module("scripts.common.terminal_output")
heading = _terminal_output.heading
item = _terminal_output.item
key_value = _terminal_output.key_value
section = _terminal_output.section
status_line = _terminal_output.status_line

_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_SCAN_ROOTS: tuple[pathlib.Path, ...] = tuple(
    root
    for root in (
        _REPO_ROOT / "src",
        _REPO_ROOT / "tests",
        _REPO_ROOT / "scripts",
        _REPO_ROOT / "examples",
    )
    if root.exists()
)
_BASELINE_ALLOWLIST_PATH: pathlib.Path = (
    _REPO_ROOT / "scripts" / "baselines" / "filename_underscore_hygiene.tsv"
)
_EXEMPT_FILENAMES: frozenset[str] = frozenset({"__init__.py", "__main__.py"})
_UNDERSCORE_THRESHOLD: int = 1
_ADVISORY_MODE: bool = True
_INFO_ONLY_ROOTS: frozenset[str] = frozenset({"tests"})


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Warn on Python filenames whose stems contain more than one underscore."
        )
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when new filename drift is detected",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="rewrite the baseline TSV from the currently detected set",
    )
    parser.add_argument(
        "--show-tests-detail",
        action="store_true",
        help="print every test-path inventory entry instead of only the summary count",
    )
    parser.add_argument(
        "--show-src-detail",
        action="store_true",
        help="print every src-path advisory entry instead of only the summary count",
    )
    return parser.parse_args(list(argv) if argv is not None else [])


def _load_allowlist(path: pathlib.Path) -> tuple[tuple[str, int], ...]:
    if not path.exists():
        return ()
    rows: list[tuple[str, int]] = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        relpath, sep, raw_count = line.partition("\t")
        if not sep or not relpath or not raw_count:
            raise ValueError(
                f"{path} line {lineno} must be '<relpath>\\t<underscore_count>'"
            )
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(
                f"{path} line {lineno} underscore count must be an integer"
            ) from exc
        rows.append((relpath, count))
    return tuple(rows)


_BASELINE_ALLOWLIST: tuple[tuple[str, int], ...] = _load_allowlist(
    _BASELINE_ALLOWLIST_PATH
)


def _display_relpath(
    path: pathlib.Path, *, repo_root: pathlib.Path, scan_root: pathlib.Path
) -> str:
    for base in (repo_root, scan_root.parent, scan_root):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _iter_python_files(scan_roots: Iterable[pathlib.Path]) -> Iterable[pathlib.Path]:
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def scan_python_files(
    *,
    scan_roots: Sequence[pathlib.Path] | None = None,
    repo_root: pathlib.Path | None = None,
    threshold: int | None = None,
) -> tuple[list[tuple[str, int]], int]:
    scan_roots = scan_roots or _SCAN_ROOTS
    repo_root = repo_root or _REPO_ROOT
    threshold = _UNDERSCORE_THRESHOLD if threshold is None else threshold
    detected: list[tuple[str, int]] = []
    scanned_files = 0
    for scan_root in scan_roots:
        for path in _iter_python_files((scan_root,)):
            scanned_files += 1
            if path.name in _EXEMPT_FILENAMES:
                continue
            count = path.stem.count("_")
            if count <= threshold:
                continue
            detected.append(
                (
                    _display_relpath(path, repo_root=repo_root, scan_root=scan_root),
                    count,
                )
            )
    return sorted(set(detected)), scanned_files


def _classify_entry(entry: tuple[str, int]) -> str:
    relpath, _count = entry
    top_level = pathlib.PurePosixPath(relpath).parts[0] if relpath else ""
    return "info" if top_level in _INFO_ONLY_ROOTS else "enforced"


def partition_entries(
    entries: Sequence[tuple[str, int]],
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    enforced: list[tuple[str, int]] = []
    info_only: list[tuple[str, int]] = []
    for entry in entries:
        target = info_only if _classify_entry(entry) == "info" else enforced
        target.append(entry)
    return sorted(enforced), sorted(info_only)


def _entries_for_root(
    entries: Sequence[tuple[str, int]], root_name: str
) -> list[tuple[str, int]]:
    return sorted(
        entry
        for entry in entries
        if pathlib.PurePosixPath(entry[0]).parts[:1] == (root_name,)
    )


def compute_drift(
    detected: list[tuple[str, int]],
    *,
    baseline: Sequence[tuple[str, int]] | None = None,
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    baseline = _BASELINE_ALLOWLIST if baseline is None else tuple(baseline)
    detected_enforced, _detected_info_only = partition_entries(detected)
    baseline_enforced, _baseline_info_only = partition_entries(baseline)
    detected_set = set(detected_enforced)
    baseline_set = set(baseline_enforced)
    return sorted(detected_set - baseline_set), sorted(baseline_set - detected_set)


def _render_entry(entry: tuple[str, int]) -> str:
    relpath, count = entry
    return f"{relpath} ({count} underscores)"


def render_report(
    *,
    detected: list[tuple[str, int]],
    new_entries: list[tuple[str, int]],
    removed_entries: list[tuple[str, int]],
    scanned_files: int,
    advisory: bool,
    strict: bool,
    show_tests_detail: bool,
    show_src_detail: bool,
    baseline: Sequence[tuple[str, int]],
    baseline_path: pathlib.Path = _BASELINE_ALLOWLIST_PATH,
) -> str:
    detected_enforced, detected_info_only = partition_entries(detected)
    baseline_enforced, baseline_info_only = partition_entries(baseline)
    detected_src = _entries_for_root(detected_enforced, "src")
    baseline_src = _entries_for_root(baseline_enforced, "src")
    lines: list[str] = []
    lines.append(heading("validate/filename_underscore_hygiene.py"))
    lines.append("")
    lines.append(section("Summary", kind="info"))
    lines.append(
        item(key_value("scan roots", [p.name for p in _SCAN_ROOTS]), prefix="  ")
    )
    lines.append(item(key_value("scanned python files", scanned_files), prefix="  "))
    lines.append(
        item(
            key_value("underscore threshold", f">{_UNDERSCORE_THRESHOLD}"), prefix="  "
        )
    )
    lines.append(item(key_value("baseline entries", len(baseline)), prefix="  "))
    lines.append(item(key_value("detected entries", len(detected)), prefix="  "))
    lines.append(
        item(
            key_value("enforced baseline entries", len(baseline_enforced)), prefix="  "
        )
    )
    lines.append(
        item(
            key_value("enforced detected entries", len(detected_enforced)), prefix="  "
        )
    )
    lines.append(
        item(key_value("src baseline entries", len(baseline_src)), prefix="  ")
    )
    lines.append(
        item(key_value("src detected entries", len(detected_src)), prefix="  ")
    )
    lines.append(
        item(key_value("tests baseline entries", len(baseline_info_only)), prefix="  ")
    )
    lines.append(
        item(key_value("tests detected entries", len(detected_info_only)), prefix="  ")
    )
    lines.append(item(key_value("advisory mode", advisory), prefix="  "))
    lines.append(item(key_value("strict mode", strict), prefix="  "))
    lines.append(item(key_value("baseline path", baseline_path), prefix="  "))
    lines.append("")
    if not new_entries and not removed_entries:
        lines.append(section("Result", kind="ok"))
        lines.append(
            status_line(
                "ok",
                "enforced filename-underscore set matches the frozen baseline.",
            )
        )
        if detected_src:
            lines.append("")
            lines.append(section("Source advisory inventory", kind="warn"))
            lines.append(
                item(
                    f"{len(detected_src)} src filename(s) still exceed the underscore practice threshold."
                )
            )
            lines.append(
                item(
                    "These stay visible as context-dependent readability/refactor candidates, even when baseline drift is zero.",
                    prefix="  ",
                )
            )
            lines.append(
                item(
                    "Use --show-src-detail to print the full src-path advisory list.",
                    prefix="  ",
                )
            )
            if show_src_detail:
                for entry in detected_src:
                    lines.append(item(_render_entry(entry), prefix="  · "))
        if detected_info_only:
            lines.append("")
            lines.append(section("Tests inventory", kind="info"))
            lines.append(
                item(
                    f"{len(detected_info_only)} test filename(s) exceed the underscore practice threshold; informational only."
                )
            )
            lines.append(
                item(
                    "Use --show-tests-detail to print the full test-path inventory.",
                    prefix="  ",
                )
            )
            if show_tests_detail:
                for entry in detected_info_only:
                    lines.append(item(_render_entry(entry), prefix="  · "))
        return "\n".join(lines) + "\n"

    lines.append("[DRIFT] filename underscore hygiene baseline drift detected.")
    if new_entries:
        lines.append("")
        lines.append(section("New multi-underscore filenames", kind="warn"))
        lines.append(
            item(
                f"{len(new_entries)} new enforced filename(s) exceed the underscore practice threshold:"
            )
        )
        for entry in new_entries:
            lines.append(item(_render_entry(entry), prefix="  + "))
    if removed_entries:
        lines.append("")
        lines.append(section("Baseline shrink", kind="ok"))
        lines.append(
            item(
                "These baseline entries no longer appear in the tree. Remove them from the TSV to lock the cleanup:"
            )
        )
        for entry in removed_entries:
            lines.append(item(_render_entry(entry), prefix="  - "))
    if detected_src:
        lines.append("")
        lines.append(section("Source advisory inventory", kind="warn"))
        lines.append(
            item(
                f"{len(detected_src)} src filename(s) still exceed the underscore practice threshold."
            )
        )
        lines.append(
            item(
                "These stay visible as context-dependent readability/refactor candidates, even when they already exist in the frozen baseline.",
                prefix="  ",
            )
        )
        lines.append(
            item(
                "Use --show-src-detail to print the full src-path advisory list.",
                prefix="  ",
            )
        )
        if show_src_detail:
            for entry in detected_src:
                lines.append(item(_render_entry(entry), prefix="  · "))
    if detected_info_only:
        lines.append("")
        lines.append(section("Tests inventory", kind="info"))
        lines.append(
            item(
                f"{len(detected_info_only)} test filename(s) exceed the underscore practice threshold; informational only."
            )
        )
        lines.append(
            item(
                "Use --show-tests-detail to print the full test-path inventory.",
                prefix="  ",
            )
        )
        if show_tests_detail:
            for entry in detected_info_only:
                lines.append(item(_render_entry(entry), prefix="  · "))
    if advisory:
        lines.append("")
        lines.append(section("Result", kind="advisory"))
        lines.append(status_line("advisory", "_ADVISORY_MODE=True"))
        lines.append(
            item(
                "New drift is reported as a naming/readability practice warning for enforced roots; test-path inventory stays informational. Use --strict to fail on enforced drift."
            )
        )
    return "\n".join(lines) + "\n"


def _write_baseline(path: pathlib.Path, detected: Sequence[tuple[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{relpath}\t{count}\n" for relpath, count in detected)
    path.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    detected, scanned_files = scan_python_files()
    baseline = _BASELINE_ALLOWLIST
    if args.write_baseline:
        _write_baseline(_BASELINE_ALLOWLIST_PATH, detected)
        baseline = tuple(detected)
    new_entries, removed_entries = compute_drift(detected, baseline=baseline)
    ok = not new_entries if args.strict else True
    report = render_report(
        detected=detected,
        new_entries=new_entries,
        removed_entries=removed_entries,
        scanned_files=scanned_files,
        advisory=_ADVISORY_MODE,
        strict=args.strict,
        show_tests_detail=args.show_tests_detail,
        show_src_detail=args.show_src_detail,
        baseline=baseline,
    )
    print(report, file=sys.stderr, end="")
    payload = {
        "validator": "validate_filename_underscore_hygiene",
        "ok": ok,
        "findings": [
            *[f"new:{relpath}:{count}" for relpath, count in new_entries],
            *[f"removed:{relpath}:{count}" for relpath, count in removed_entries],
        ],
        "metrics": {
            "scanned_files": scanned_files,
            "threshold": _UNDERSCORE_THRESHOLD,
            "baseline_entries": len(baseline),
            "detected_entries": len(detected),
            "new_entries": len(new_entries),
            "removed_entries": len(removed_entries),
            "enforced_baseline_entries": len(partition_entries(baseline)[0]),
            "enforced_detected_entries": len(partition_entries(detected)[0]),
            "src_baseline_entries": len(
                _entries_for_root(partition_entries(baseline)[0], "src")
            ),
            "src_detected_entries": len(
                _entries_for_root(partition_entries(detected)[0], "src")
            ),
            "tests_baseline_entries": len(partition_entries(baseline)[1]),
            "tests_detected_entries": len(partition_entries(detected)[1]),
        },
        "strict": args.strict,
        "advisory_mode": _ADVISORY_MODE,
    }
    print(json.dumps(payload, sort_keys=True))
    return 1 if args.strict and new_entries else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
