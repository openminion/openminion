#!/usr/bin/env python3
"""Detect module public-surface imports that reach internal subpackages."""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys

_SCRIPT_ROOT = pathlib.Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

REPO_IMPORT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

collect_top_level_import_targets = importlib.import_module(
    "scripts.common.ast_imports"
).collect_top_level_import_targets
_terminal_output = importlib.import_module("scripts.common.terminal_output")
heading = _terminal_output.heading
item = _terminal_output.item
key_value = _terminal_output.key_value
section = _terminal_output.section
status_line = _terminal_output.status_line

_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_SOURCE_ROOT: pathlib.Path = _REPO_ROOT / "src" / "openminion"
_MODULES_ROOT: pathlib.Path = _SOURCE_ROOT / "modules"

# Internal-by-default subpackage names under ``modules/<name>/``.
_INTERNAL_SUBPACKAGES: frozenset[str] = frozenset({"runtime", "storage", "providers"})

_ADVISORY_MODE: bool = True

# Categories listed here fail CI; omitted categories stay advisory.
_FAIL_CATEGORIES: frozenset[str] = frozenset()

# Frozen baseline of detected (importer_relpath, target_dotted_path) reaches.
# TSV shape per line: <importer_relpath_from_src_openminion>	<target_full_dotted_module>
_BASELINE_ALLOWLIST_PATH: pathlib.Path = (
    _REPO_ROOT / "scripts" / "baselines" / "public_surface_allowlist.tsv"
)


def _load_allowlist(path: pathlib.Path) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for lineno, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        importer, sep, target = line.partition("	")
        if not sep or not importer or not target:
            raise ValueError(
                f"{path} line {lineno} must be '<importer_relpath>\t<target_module>'"
            )
        rows.append((importer, target))
    return tuple(rows)


_BASELINE_ALLOWLIST: tuple[tuple[str, str], ...] = _load_allowlist(
    _BASELINE_ALLOWLIST_PATH
)


def _iter_top_level_modules(modules_root: pathlib.Path) -> set[str]:
    return {
        p.name for p in modules_root.iterdir() if p.is_dir() and p.name != "__pycache__"
    }


def _importer_top_module(rel_parts: tuple[str, ...]) -> str | None:
    """Return the importer file's top-level modules-package name, if any.

    For ``modules/<name>/...`` files, returns ``<name>``. For files outside
    ``modules/`` (e.g. ``services/...``, ``cli/...``, ``api/...``,
    ``tools/...``), returns ``None`` (those are not in any modules/ package, so
    every reach into ``modules/<x>/<internal>/`` counts).
    """
    if len(rel_parts) >= 2 and rel_parts[0] == "modules":
        return rel_parts[1]
    return None


def scan_reaches(
    source_root: pathlib.Path = _SOURCE_ROOT,
) -> list[tuple[str, str]]:
    """Walk the source tree and return all (importer, target) internal-path
    reaches under the locked detection methodology.

    Returns a sorted, de-duplicated list of ``(importer_relpath, target_dotted)``
    tuples.
    """
    modules_root = source_root / "modules"
    top_modules = _iter_top_level_modules(modules_root)
    reaches: set[tuple[str, str]] = set()

    for py_path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        try:
            source = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(py_path))
        except SyntaxError:
            continue
        rel = py_path.relative_to(source_root).as_posix()
        rel_parts = pathlib.Path(rel).parts
        importer_module = _importer_top_module(rel_parts)
        for target in collect_top_level_import_targets(tree):
            if not target.startswith("openminion.modules."):
                continue
            tparts = target.split(".")
            # Need at least openminion.modules.<name>.<internal> (4 parts).
            if len(tparts) < 4:
                continue
            target_module = tparts[2]
            target_internal = tparts[3]
            if target_module not in top_modules:
                continue
            if target_internal not in _INTERNAL_SUBPACKAGES:
                continue
            # Self-imports (same top-level modules/<name>) are intra-module —
            # not boundary violations.
            if importer_module is not None and importer_module == target_module:
                continue
            reaches.add((rel, target))

    return sorted(reaches)


def _category_for(target: str) -> str | None:
    parts = target.split(".")
    if len(parts) >= 4 and parts[0] == "openminion" and parts[1] == "modules":
        sub = parts[3]
        if sub in _INTERNAL_SUBPACKAGES:
            return sub
    return None


def compute_drift(
    detected: list[tuple[str, str]],
    baseline: tuple[tuple[str, str], ...] = _BASELINE_ALLOWLIST,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    detected_set = set(detected)
    baseline_set = set(baseline)
    new = sorted(detected_set - baseline_set)
    removed = sorted(baseline_set - detected_set)
    return new, removed


def _render_reach(reach: tuple[str, str]) -> str:
    return f"{reach[0]} -> {reach[1]}"


def _category_counts(reaches: list[tuple[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(sorted(_INTERNAL_SUBPACKAGES), 0)
    for _, target in reaches:
        cat = _category_for(target)
        if cat is not None:
            counts[cat] = counts.get(cat, 0) + 1
    return counts


def render_report(
    detected: list[tuple[str, str]],
    new_reaches: list[tuple[str, str]],
    removed_reaches: list[tuple[str, str]],
    advisory: bool,
    fail_categories: frozenset[str],
) -> str:
    lines: list[str] = []
    lines.append(heading("validate/public_surface.py"))
    lines.append("")
    lines.append(section("Summary", kind="info"))
    lines.append(item(key_value("source root", _SOURCE_ROOT), prefix="  "))
    lines.append(
        item(
            key_value("internal subpackages", sorted(_INTERNAL_SUBPACKAGES)),
            prefix="  ",
        )
    )
    lines.append(item(key_value("advisory mode", advisory), prefix="  "))
    lines.append(
        item(
            key_value(
                "fail categories",
                sorted(fail_categories) if fail_categories else "[] (all advisory)",
            ),
            prefix="  ",
        )
    )
    lines.append(
        item(key_value("baseline reaches", len(_BASELINE_ALLOWLIST)), prefix="  ")
    )
    lines.append(item(key_value("detected reaches", len(detected)), prefix="  "))
    cat_counts = _category_counts(detected)
    lines.append(
        item(
            key_value(
                "detected by category",
                ", ".join(f"{k}={v}" for k, v in sorted(cat_counts.items())),
            ),
            prefix="  ",
        )
    )
    lines.append("")
    if not new_reaches and not removed_reaches:
        lines.append(section("Result", kind="ok"))
        lines.append(
            status_line("ok", "detected internal-reach set matches baseline allowlist.")
        )
        lines.append(item("detected internal-reach set matches baseline allowlist."))
        return "\n".join(lines) + "\n"
    lines.append("[DRIFT] public-surface baseline drift detected.")
    if new_reaches:
        lines.append("")
        lines.append(section("New internal-path reaches", kind="fail"))
        lines.append(
            item(
                f"{len(new_reaches)} new internal-path reach(es) detected (not in baseline):"
            )
        )
        for reach in new_reaches:
            cat = _category_for(reach[1]) or "?"
            lines.append(item(f"[{cat}] {_render_reach(reach)}", prefix="  + "))
    if removed_reaches:
        lines.append("")
        lines.append(section("Baseline shrink", kind="warn"))
        lines.append(
            item(f"{len(removed_reaches)} baseline reach(es) no longer detected:")
        )
        lines.append(
            item(
                "Remove the entry from scripts/baselines/public_surface_allowlist.tsv to lock the shrink.",
                prefix="  ",
            )
        )
        for reach in removed_reaches:
            cat = _category_for(reach[1]) or "?"
            lines.append(item(f"[{cat}] {_render_reach(reach)}", prefix="  - "))
    if advisory:
        lines.append("")
        lines.append(section("Result", kind="advisory"))
        lines.append(status_line("advisory", "_ADVISORY_MODE=True"))
        lines.append(
            item("drift is reported but exit code is governed by _FAIL_CATEGORIES.")
        )
        if fail_categories:
            lines.append(
                item(
                    f"Fail categories ({sorted(fail_categories)}) still cause exit 1 for drift in those categories; other categories are advisory."
                )
            )
        else:
            lines.append(
                item(
                    "Flip _ADVISORY_MODE=False (or seed _FAIL_CATEGORIES) in this script to fail CI on drift."
                )
            )
    return "\n".join(lines) + "\n"


def _drift_should_fail(
    new_reaches: list[tuple[str, str]],
    removed_reaches: list[tuple[str, str]],
    advisory: bool,
    fail_categories: frozenset[str],
) -> bool:
    if not new_reaches and not removed_reaches:
        return False
    if not advisory:
        return True
    if not fail_categories:
        return False
    for reach in new_reaches + removed_reaches:
        cat = _category_for(reach[1])
        if cat is not None and cat in fail_categories:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    detected = scan_reaches(_SOURCE_ROOT)
    new_reaches, removed_reaches = compute_drift(detected, _BASELINE_ALLOWLIST)
    report = render_report(
        detected, new_reaches, removed_reaches, _ADVISORY_MODE, _FAIL_CATEGORIES
    )
    sys.stdout.write(report)
    if _drift_should_fail(
        new_reaches, removed_reaches, _ADVISORY_MODE, _FAIL_CATEGORIES
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
