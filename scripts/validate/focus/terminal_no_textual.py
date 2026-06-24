"""Guard `terminal` against direct `textual` imports."""

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
FOCUS_TERMINAL_DIR = REPO_ROOT / "src" / "openminion" / "cli" / "tui" / "terminal"
BASELINE_PATH = REPO_ROOT / "scripts" / "baselines" / "terminal_no_textual_baseline.txt"


@dataclass(frozen=True)
class Violation:
    """One forbidden-textual-import detected in a terminal file."""

    file: str  # repo-relative path
    line: int
    module: str  # the `textual...` module path
    symbol: str | None  # imported name when ImportFrom; None for plain Import

    def as_baseline_line(self) -> str:
        sym = self.symbol or "*"
        return f"{self.file}:{self.line}:{self.module}:{sym}"


def _is_textual_module(module: str | None) -> bool:
    return bool(module) and (module == "textual" or module.startswith("textual."))


def _scan_file(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"validate_terminal_no_textual: cannot read {path}: {exc}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(f"validate_terminal_no_textual: cannot parse {path}: {exc}")

    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_textual_module(node.module):
                for alias in node.names:
                    violations.append(
                        Violation(
                            file=rel,
                            line=node.lineno,
                            module=node.module or "",
                            symbol=alias.name,
                        )
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_textual_module(alias.name):
                    violations.append(
                        Violation(
                            file=rel,
                            line=node.lineno,
                            module=alias.name,
                            symbol=None,
                        )
                    )
    return violations


def _scan_terminal_surface() -> list[Violation]:
    violations: list[Violation] = []
    if FOCUS_TERMINAL_DIR.is_dir():
        for child in sorted(FOCUS_TERMINAL_DIR.rglob("*.py")):
            violations.extend(_scan_file(child))
    return violations


def _load_baseline() -> set[str]:
    return load_baseline_lines(BASELINE_PATH)


def _write_baseline(violations: list[Violation]) -> None:
    header = (
        "# Pinned baseline of accepted textual.* imports under\n"
        "# `cli/tui/terminal/`. Managed by\n"
        "# `scripts/validate/focus/terminal_no_textual.py`.\n"
        "# This list MUST stay empty — terminal-flow code is forbidden\n"
        "# from importing textual.\n"
        "# Format: <file>:<line>:<module>:<symbol>\n"
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

    violations = _scan_terminal_surface()

    if args.update_baseline:
        _write_baseline(violations)
        print(
            f"validate_terminal_no_textual: baseline rewritten with "
            f"{len(violations)} violation(s)."
        )
        return 0

    baseline = _load_baseline()
    current = {v.as_baseline_line() for v in violations}
    new_violations = sorted(current - baseline)

    if new_violations:
        print(
            "validate_terminal_no_textual: NEW textual.* imports",
            file=sys.stderr,
        )
        print(
            "(not in baseline) detected — terminal-flow shell must NOT",
            file=sys.stderr,
        )
        print("import textual:", file=sys.stderr)
        for entry in new_violations:
            print(f"  {entry}", file=sys.stderr)
        return 1

    print(
        f"validate_terminal_no_textual: clean — "
        f"{len(violations)} violation(s) match baseline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
