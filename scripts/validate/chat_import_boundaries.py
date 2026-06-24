"""Guard chat-surface import boundaries against new `openminion.modules.*` reaches."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.baseline_files import load_baseline_lines, write_baseline_lines  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_DIR = REPO_ROOT / "src" / "openminion" / "cli" / "chat"
BASELINE_PATH = (
    REPO_ROOT / "scripts" / "baselines" / "chat_import_boundaries_baseline.txt"
)
MODULES_PREFIX = "openminion.modules."


@dataclass(frozen=True)
class Violation:
    """One chat→modules import violation."""

    file: str
    line: int
    module: str
    symbol: str | None

    def as_baseline_line(self) -> str:
        return f"{self.file}:{self.line}:{self.module}:{self.symbol or '*'}"


def _is_modules_submodule(module: str | None) -> bool:
    return bool(module) and module.startswith(MODULES_PREFIX)


def _scan_file(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"validate_chat_import_boundaries: cannot read {path}: {exc}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(f"validate_chat_import_boundaries: cannot parse {path}: {exc}")

    rel = (
        path.relative_to(REPO_ROOT).as_posix()
        if path.is_relative_to(REPO_ROOT)
        else path.as_posix()
    )

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_modules_submodule(node.module):
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
                if _is_modules_submodule(alias.name):
                    violations.append(
                        Violation(
                            file=rel,
                            line=node.lineno,
                            module=alias.name,
                            symbol=None,
                        )
                    )
    return violations


def _scan_chat_surface() -> list[Violation]:
    violations: list[Violation] = []
    if not CHAT_DIR.is_dir():
        return violations
    for child in sorted(CHAT_DIR.rglob("*.py")):
        violations.extend(_scan_file(child))
    return violations


def _load_baseline() -> set[str]:
    return load_baseline_lines(BASELINE_PATH)


def _write_baseline(violations: list[Violation]) -> None:
    header = (
        "# Pinned baseline of accepted `cli/chat/` ->\n"
        "# `openminion.modules.*` import reaches.\n"
        "# Managed by `scripts/validate/chat_import_boundaries.py`.\n"
        "# Existing reaches are tracked here for compatibility, but NEW\n"
        "# reaches must fail CI.\n"
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

    violations = _scan_chat_surface()

    if args.update_baseline:
        _write_baseline(violations)
        print(
            "validate_chat_import_boundaries: baseline rewritten with "
            f"{len(violations)} violation(s)."
        )
        return 0

    baseline = _load_baseline()
    current = {violation.as_baseline_line() for violation in violations}
    new_violations = sorted(current - baseline)
    stale_baseline = sorted(baseline - current)

    if new_violations:
        print(
            "validate_chat_import_boundaries: NEW chat->modules imports "
            "(not in baseline) detected:",
            file=sys.stderr,
        )
        for entry in new_violations:
            print(f"  {entry}", file=sys.stderr)
        return 1

    if stale_baseline:
        print(
            "validate_chat_import_boundaries: baseline drift detected "
            "(entries no longer present in source):",
            file=sys.stderr,
        )
        for entry in stale_baseline:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Run `scripts/validate/chat_import_boundaries.py --update-baseline` "
            "to accept the shrink.",
            file=sys.stderr,
        )
        return 1

    print(
        "validate_chat_import_boundaries: clean — "
        f"{len(violations)} violation(s) match baseline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
