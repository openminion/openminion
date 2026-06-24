#!/usr/bin/env python3.11
"""Reject bare built-in raises in typed module owners."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import heading, item, section  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openminion" / "modules"

MODULES = ("memory", "llm", "tool")

_RAISE_RE = re.compile(r"raise (ValueError|RuntimeError|TypeError)\(")
_PRAGMA_RE = re.compile(r"#\s*allow-bare-raise:\s*\S")


def _expression_has_pragma(lines: list[str], start_idx: int) -> bool:
    # Count parens to find the end of the expression.
    open_count = 0
    saw_open = False
    end_idx = start_idx
    for idx in range(start_idx, min(start_idx + 50, len(lines))):
        line = lines[idx]
        for ch in line:
            if ch == "(":
                open_count += 1
                saw_open = True
            elif ch == ")":
                open_count -= 1
        if saw_open and open_count <= 0:
            end_idx = idx
            break
        end_idx = idx
    for idx in range(start_idx, end_idx + 1):
        if _PRAGMA_RE.search(lines[idx]):
            return True
    return False


def scan_module(module_root: Path) -> tuple[list[tuple[Path, int, str]], int]:
    """Walk a module subtree and return (violations, exemption_count)."""
    violations: list[tuple[Path, int, str]] = []
    exemption_count = 0
    for py_file in sorted(module_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines):
            if not _RAISE_RE.search(line):
                continue
            if _expression_has_pragma(lines, idx):
                exemption_count += 1
                continue
            violations.append((py_file, idx + 1, line.rstrip()))
    return violations, exemption_count


def main() -> int:
    if not SRC_ROOT.exists():
        print(
            f"validate_module_typed_raises: src root not found: {SRC_ROOT}",
            file=sys.stderr,
        )
        return 1

    total_violations: list[tuple[Path, int, str]] = []
    exemption_totals: dict[str, int] = {}

    for module_name in MODULES:
        module_root = SRC_ROOT / module_name
        if not module_root.exists():
            print(
                f"validate_module_typed_raises: module not found: {module_root}",
                file=sys.stderr,
            )
            return 1
        violations, exemption_count = scan_module(module_root)
        total_violations.extend(violations)
        exemption_totals[module_name] = exemption_count

    if total_violations:
        print(
            heading("validate_module_typed_raises", stream=sys.stderr), file=sys.stderr
        )
        print(
            "",
            file=sys.stderr,
        )
        print(section("Findings", kind="fail", stream=sys.stderr), file=sys.stderr)
        print(
            item(
                "found bare raises without the `# allow-bare-raise: <rationale>` pragma in the scoped modules."
            ),
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        for path, line_no, line in total_violations:
            rel = path.relative_to(REPO_ROOT)
            print(
                item(f"{rel}:{line_no}: {line.strip()}", prefix="  "), file=sys.stderr
            )
        print("", file=sys.stderr)
        print(section("Resolution", kind="info", stream=sys.stderr), file=sys.stderr)
        print(
            item("Each bare raise inside `modules/{memory,llm,tool}/` must either:"),
            file=sys.stderr,
        )
        print(
            item(
                "use the module's own typed error class (`MemctlError` subclass / `LLMCtlError` / `ToolRuntimeError`), or"
            ),
            file=sys.stderr,
        )
        print(
            item("carry a same-line `# allow-bare-raise: <rationale>` pragma."),
            file=sys.stderr,
        )
        print(
            item("See the typed error adoption rule."),
            file=sys.stderr,
        )
        return 1

    print(heading("validate_module_typed_raises"))
    print("")
    print(section("Result", kind="ok"))
    print(item("typed bare-raise guard is clean."))
    print(
        item(
            "Exemption (pragma) counts: "
            + ", ".join(f"{name}={exemption_totals[name]}" for name in MODULES)
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
