#!/usr/bin/env python3
"""Detect top-level circular imports across OpenMinion module packages."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
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

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_MODULES_ROOT: Path = _REPO_ROOT / "src" / "openminion" / "modules"
_MAX_CYCLE_LEN: int = 5
_ADVISORY_MODE: bool = False

# Canonicalized cycles ending with the duplicate start node. Empty means any
# detected module-level cycle is a regression.
_BASELINE_ALLOWLIST: tuple[tuple[str, ...], ...] = ()


def _iter_top_level_modules(modules_root: Path) -> list[str]:
    return sorted(
        p.name for p in modules_root.iterdir() if p.is_dir() and p.name != "__pycache__"
    )


def build_module_graph(modules_root: Path = _MODULES_ROOT) -> dict[str, set[str]]:
    top_level = _iter_top_level_modules(modules_root)
    top_set = set(top_level)
    graph: dict[str, set[str]] = {name: set() for name in top_level}

    for module_name in top_level:
        module_root = modules_root / module_name
        for py_path in sorted(module_root.rglob("*.py")):
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
            for target in collect_top_level_import_targets(tree):
                if not target.startswith("openminion.modules."):
                    continue
                parts = target.split(".")
                if len(parts) < 3:
                    continue
                head = parts[2]
                if head in top_set and head != module_name:
                    graph[module_name].add(head)

    return graph


def find_simple_cycles(
    graph: dict[str, set[str]],
    max_len: int = _MAX_CYCLE_LEN,
) -> list[tuple[str, ...]]:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))

    nodes = sorted(graph.keys())
    found: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    def canon(body: tuple[str, ...]) -> tuple[str, ...]:
        n = len(body)
        rotations = [tuple(body[i:] + body[:i]) for i in range(n)]
        best = min(rotations)
        return best + (best[0],)

    def dfs(start: str, current: str, path: list[str], visited: set[str]) -> None:
        if len(path) > max_len:
            return
        for nxt in sorted(graph.get(current, set())):
            if nxt == start and len(path) >= 2:
                canonical = canon(tuple(path))
                if canonical not in seen:
                    seen.add(canonical)
                    found.append(canonical)
            elif nxt not in visited and nxt > start:
                visited.add(nxt)
                dfs(start, nxt, path + [nxt], visited)
                visited.discard(nxt)

    for start in nodes:
        dfs(start, start, [start], {start})

    found.sort(key=lambda cyc: (len(cyc), cyc))
    return found


def _render_cycle(cyc: tuple[str, ...]) -> str:
    return " -> ".join(cyc)


def compute_drift(
    detected: list[tuple[str, ...]],
    baseline: tuple[tuple[str, ...], ...] = _BASELINE_ALLOWLIST,
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    detected_set = set(detected)
    baseline_set = set(baseline)
    new = sorted(detected_set - baseline_set, key=lambda c: (len(c), c))
    removed = sorted(baseline_set - detected_set, key=lambda c: (len(c), c))
    return new, removed


def render_report(
    detected: list[tuple[str, ...]],
    new_cycles: list[tuple[str, ...]],
    removed_cycles: list[tuple[str, ...]],
    advisory: bool,
) -> str:
    lines: list[str] = []
    lines.append(heading("validate/module_cycles.py"))
    lines.append("")
    lines.append(section("Summary", kind="info"))
    lines.append(item(key_value("modules root", _MODULES_ROOT), prefix="  "))
    lines.append(item(key_value("max cycle length", _MAX_CYCLE_LEN), prefix="  "))
    lines.append(item(key_value("advisory mode", advisory), prefix="  "))
    lines.append(
        item(key_value("baseline cycles", len(_BASELINE_ALLOWLIST)), prefix="  ")
    )
    lines.append(item(key_value("detected cycles", len(detected)), prefix="  "))
    lines.append("")
    if not new_cycles and not removed_cycles:
        lines.append(section("Result", kind="ok"))
        lines.append(
            status_line("ok", "detected cycle set matches baseline allowlist.")
        )
        lines.append(item("detected cycle set matches baseline allowlist."))
        return "\n".join(lines) + "\n"
    lines.append("[DRIFT] cycle baseline drift detected.")
    if new_cycles:
        lines.append("")
        lines.append(section("New cycles", kind="fail"))
        lines.append(
            item(f"{len(new_cycles)} new cycle(s) detected (not in baseline):")
        )
        for cyc in new_cycles:
            lines.append(item(_render_cycle(cyc), prefix="  + "))
    if removed_cycles:
        lines.append("")
        lines.append(section("Baseline shrink", kind="warn"))
        lines.append(
            item(f"{len(removed_cycles)} baseline cycle(s) no longer detected:")
        )
        lines.append(
            item(
                "Remove the entry from _BASELINE_ALLOWLIST in this script to lock the shrink.",
                prefix="  ",
            )
        )
        for cyc in removed_cycles:
            lines.append(item(_render_cycle(cyc), prefix="  - "))
    if advisory:
        lines.append("")
        lines.append(section("Result", kind="advisory"))
        lines.append(status_line("advisory", "_ADVISORY_MODE=True"))
        lines.append(item("drift is reported but exit code is 0."))
        lines.append(
            item("Flip _ADVISORY_MODE=False in this script to fail CI on drift.")
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    graph = build_module_graph(_MODULES_ROOT)
    detected = find_simple_cycles(graph, max_len=_MAX_CYCLE_LEN)
    new_cycles, removed_cycles = compute_drift(detected, _BASELINE_ALLOWLIST)
    report = render_report(detected, new_cycles, removed_cycles, _ADVISORY_MODE)
    sys.stdout.write(report)
    if not new_cycles and not removed_cycles:
        return 0
    if _ADVISORY_MODE:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
