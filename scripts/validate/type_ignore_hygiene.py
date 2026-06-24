#!/usr/bin/env python3
"""Require coded `# type: ignore[...]` pragmas with stable reporting."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Match `# type: ignore` optionally followed by `[code1,code2,...]`.
# A bare `# type: ignore` has no `[code]` qualifier.
# Capture group 1 = the qualifier (or empty string when bare).
_TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore(?:\[([^\]]+)\])?", re.IGNORECASE)


def _iter_python_files(src_root: Path):
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _scan_file(path: Path) -> tuple[list[int], list[tuple[int, list[str]]]]:
    bare: list[int] = []
    coded: list[tuple[int, list[str]]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return bare, coded
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _TYPE_IGNORE_RE.finditer(line):
            qualifier = match.group(1)
            if qualifier is None:
                bare.append(lineno)
            else:
                codes = [c.strip() for c in qualifier.split(",") if c.strip()]
                coded.append((lineno, codes))
    return bare, coded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate `# type: ignore` comments use explicit error codes.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Reject bare `# type: ignore` (default)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Emit baseline JSON (warn-only) and exit 0",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    if not src_root.is_dir():
        print(f"src/ not found at {src_root}", file=sys.stderr)
        return 1

    bare_violations: list[tuple[str, int]] = []
    code_counts: dict[str, int] = {}
    file_density: dict[str, int] = {}

    for py_file in _iter_python_files(src_root):
        rel = str(py_file.relative_to(src_root))
        bare, coded = _scan_file(py_file)
        for lineno in bare:
            bare_violations.append((rel, lineno))
        for _lineno, codes in coded:
            file_density[rel] = file_density.get(rel, 0) + len(codes)
            for code in codes:
                code_counts[code] = code_counts.get(code, 0) + 1

    if args.baseline:
        baseline_path = (
            repo_root / "scripts" / "baselines" / "type_ignore_baseline.json"
        )
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "code_counts": dict(sorted(code_counts.items(), key=lambda kv: -kv[1])),
            "file_density_top": dict(
                sorted(file_density.items(), key=lambda kv: -kv[1])[:50]
            ),
            "bare_count": len(bare_violations),
        }
        baseline_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(
            "[type-ignore-hygiene] baseline written → "
            f"{baseline_path.relative_to(repo_root)} "
            f"({len(code_counts)} codes, top file density "
            f"{sorted(file_density.values(), reverse=True)[:1]})"
        )
        return 0

    # Strict mode (default): reject bare ignores.
    if bare_violations:
        print(
            f"type-ignore hygiene: {len(bare_violations)} bare `# type: ignore` "
            "without `[code]` qualifier:",
            file=sys.stderr,
        )
        for rel, lineno in bare_violations[:20]:
            print(f"  {rel}:{lineno}", file=sys.stderr)
        if len(bare_violations) > 20:
            print(
                f"  ... and {len(bare_violations) - 20} more",
                file=sys.stderr,
            )
        print(
            "\nRemediation: change every `# type: ignore` to "
            "`# type: ignore[<code>]` with the exact mypy error code.",
            file=sys.stderr,
        )
        return 1

    total = sum(code_counts.values())
    print(
        "[type-ignore-hygiene] clean — "
        "0 bare ignores; "
        f"{total} qualified ignores across {len(code_counts)} codes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
