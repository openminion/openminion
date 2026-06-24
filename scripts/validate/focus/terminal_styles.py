"""Guard `terminal` against hardcoded Rich color names."""

from __future__ import annotations

import argparse
import re
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
BASELINE_PATH = REPO_ROOT / "scripts" / "baselines" / "terminal_styles_baseline.txt"


# Pattern: `style="(bold|dim|italic )*(color)"` where color is one of
# the Rich color names we forbid.
_HARDCODED_COLORS = (
    "red",
    "green",
    "yellow",
    "cyan",
    "magenta",
    "blue",
    "white",
    "bright_red",
    "bright_green",
    "bright_yellow",
    "bright_cyan",
    "bright_magenta",
    "bright_blue",
    "bright_white",
)
_PATTERN = re.compile(
    r"""style=["'](?:(?:bold|dim|italic|underline)\s+)*("""
    + "|".join(_HARDCODED_COLORS)
    + r""")(?:\s+(?:bold|dim|italic|underline))*["']"""
)


@dataclass(frozen=True)
class Violation:
    """One hardcoded-style use detected in a terminal file."""

    file: str  # repo-relative path
    line: int
    color: str

    def as_baseline_line(self) -> str:
        return f"{self.file}:{self.line}:{self.color}"


def _scan_file(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"validate_terminal_styles: cannot read {path}: {exc}")
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    violations: list[Violation] = []
    for line_idx, line in enumerate(source.splitlines(), start=1):
        for match in _PATTERN.finditer(line):
            violations.append(Violation(file=rel, line=line_idx, color=match.group(1)))
    return violations


def _scan_dir(root: Path) -> list[Violation]:
    if not root.is_dir():
        return []
    violations: list[Violation] = []
    for py in sorted(root.rglob("*.py")):
        violations.extend(_scan_file(py))
    return violations


def _load_baseline() -> set[str]:
    return load_baseline_lines(BASELINE_PATH)


def _write_baseline(violations: list[Violation]) -> None:
    write_baseline_lines(
        BASELINE_PATH,
        (v.as_baseline_line() for v in violations),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Forbid hardcoded Rich color-name styles under cli/tui/terminal/.")
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the baseline file with the current violations.",
    )
    args = parser.parse_args(argv)

    violations = _scan_dir(FOCUS_TERMINAL_DIR)
    if args.update_baseline:
        _write_baseline(violations)
        print(
            f"validate_terminal_styles: "
            f"baseline updated with {len(violations)} entr"
            f"{'y' if len(violations) == 1 else 'ies'}."
        )
        return 0

    baseline = _load_baseline()
    current = {v.as_baseline_line() for v in violations}
    new_violations = current - baseline
    if not new_violations:
        print(
            f"validate_terminal_styles: "
            f"clean — {len(current)} violation(s) match baseline."
        )
        return 0

    print(
        f"validate_terminal_styles: "
        f"{len(new_violations)} new hardcoded Rich style(s) detected:"
    )
    for entry in sorted(new_violations):
        print(f"  + {entry}")
    print(
        "Route through `style_token()` / `token_rich_style()` from "
        "`cli/presentation/styles.py` + `cli/tui/presentation/markers.py`. "
        "See the CLI color contract."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
