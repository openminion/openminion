"""Guard focus-shell widgets from depending on dashboard body widgets."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

try:  # noqa: E402
    from scripts.common.baseline_files import load_baseline_lines, write_baseline_lines
except ModuleNotFoundError:  # pragma: no cover - standalone copied-script fallback
    fallback_root = next(
        (
            candidate
            for candidate in (
                Path(__file__).resolve().parent,
                *Path(__file__).resolve().parents,
            )
            if (candidate / "common").is_dir()
        ),
        None,
    )
    if fallback_root is not None and str(fallback_root) not in sys.path:
        sys.path.insert(0, str(fallback_root))
    from common.baseline_files import load_baseline_lines, write_baseline_lines

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = next(
    (
        candidate
        for candidate in (SCRIPT_DIR, *SCRIPT_DIR.parents)
        if (candidate / "src" / "openminion").is_dir()
        and (candidate / "scripts" / "baselines").is_dir()
    ),
    Path(__file__).resolve().parents[3],
)
FOCUS_SCREEN = REPO_ROOT / "src" / "openminion" / "cli" / "interactive" / "screen.py"
FOCUS_WIDGETS_DIR = (
    REPO_ROOT / "src" / "openminion" / "cli" / "interactive" / "widgets"
)
BASELINE_PATH = (
    REPO_ROOT / "scripts" / "baselines" / "focus_widget_isolation_baseline.txt"
)

FORBIDDEN_SYMBOLS = frozenset({"ChatView", "ChatInputBar", "MessageWidget"})


@dataclass(frozen=True)
class Violation:
    """One forbidden-symbol import detected in a focus file."""

    file: str  # repo-relative path
    line: int
    symbol: str

    def as_baseline_line(self) -> str:
        return f"{self.file}:{self.line}:{self.symbol}"


def _scan_file(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"validate_focus_widget_isolation: cannot read {path}: {exc}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(f"validate_focus_widget_isolation: cannot parse {path}: {exc}")

    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Path is outside REPO_ROOT — happens when unit tests pass
        # synthetic fixtures from `tmp_path`. Fall back to the
        # absolute path string so violations remain attributable.
        rel = path.as_posix()
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            name = alias.name
            if name in FORBIDDEN_SYMBOLS:
                violations.append(Violation(file=rel, line=node.lineno, symbol=name))
    return violations


def _scan_focus_surface() -> list[Violation]:
    """Scan the canonical interactive screen and its widgets."""
    violations: list[Violation] = []
    if FOCUS_SCREEN.is_file():
        violations.extend(_scan_file(FOCUS_SCREEN))
    if FOCUS_WIDGETS_DIR.is_dir():
        for child in sorted(FOCUS_WIDGETS_DIR.rglob("*.py")):
            violations.extend(_scan_file(child))
    return violations


def _load_baseline() -> set[str]:
    """Load the pinned baseline of accepted violations.

    Missing baseline file is treated as empty.
    """
    return load_baseline_lines(BASELINE_PATH)


def _write_baseline(violations: list[Violation]) -> None:
    header = (
        "# Pinned baseline of accepted forbidden-import violations\n"
        "# in the focus shell. Managed by\n"
        "# `scripts/validate/focus/widget_isolation.py`.\n"
        "# This list should shrink as native widgets replace shared ones.\n"
        "# The validator fails\n"
        "# CI if any current violation is not present in this baseline.\n"
        "# Format: <file>:<line>:<symbol>\n"
    )
    write_baseline_lines(
        BASELINE_PATH,
        (v.as_baseline_line() for v in violations),
        header=header,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline file to match the current violation set.",
    )
    args = parser.parse_args(argv)

    violations = _scan_focus_surface()

    if args.update_baseline:
        _write_baseline(violations)
        print(
            f"validate_focus_widget_isolation: baseline rewritten with "
            f"{len(violations)} violation(s)."
        )
        return 0

    baseline = _load_baseline()
    current = {v.as_baseline_line() for v in violations}
    new_violations = sorted(current - baseline)
    stale_baseline = sorted(baseline - current)

    if new_violations:
        print(
            "validate_focus_widget_isolation: NEW forbidden-symbol imports",
            file=sys.stderr,
        )
        print(
            "(not in baseline) detected — focus shell must own its body widgets:",
            file=sys.stderr,
        )
        for entry in new_violations:
            print(f"  {entry}", file=sys.stderr)
        print(
            "\nIf the current source intentionally shrank the baseline, run\n"
            "`.venv/bin/python3.11 scripts/validate/focus/widget_isolation.py "
            "--update-baseline` to regenerate.",
            file=sys.stderr,
        )
        return 1

    if stale_baseline:
        # Stale entries mean the baseline shrank — print as a hint
        # so the round can shrink it explicitly via --update-baseline.
        print(
            "validate_focus_widget_isolation: baseline contains stale entries",
            file=sys.stderr,
        )
        print(
            "(no longer present in source — consider --update-baseline):",
            file=sys.stderr,
        )
        for entry in stale_baseline:
            print(f"  {entry}", file=sys.stderr)
        # Stale entries are NOT a hard failure — only new violations are.

    print(
        f"validate_focus_widget_isolation: clean — "
        f"{len(violations)} violation(s) match baseline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
