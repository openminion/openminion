"""Protect loop-tool step ownership from runtime-authored prose."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import (  # noqa: E402
    emit_grouped_path_findings,
    heading,
    status_line,
)


_SCAN_TARGETS: tuple[Path, ...] = (
    Path("src/openminion/modules/brain/loop/tools/engine.py"),
    Path("src/openminion/modules/brain/loop/tools/direct_tool.py"),
)


# Banned phrases (case-insensitive substring match).
_BANNED_PHRASES: tuple[str, ...] = (
    "answer the user",
    "answer the user directly",
    "choose a different next step",
    "ask the user for the missing value",
    "retry with corrected arguments",
    "call the tool again with corrected arguments",
    "use the existing tool results",
)


def _scan_file(path: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # Allow references inside comments so explanatory notes do not trip the
        # guard.
        if stripped.startswith("#"):
            continue
        lowered = line.lower()
        for phrase in _BANNED_PHRASES:
            if phrase in lowered:
                findings.append((path, idx, phrase))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Override scan targets (advanced — leave empty for CI defaults).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit findings as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    targets = tuple(args.paths) if args.paths else _SCAN_TARGETS
    existing = [t for t in targets if t.is_file()]
    if not existing:
        print(heading("validate_runtime_step_ownership"))
        print(
            status_line(
                "warn",
                f"no scan targets (looked for {[str(t) for t in targets]})",
            )
        )
        return 0

    findings: list[tuple[Path, int, str]] = []
    for target in existing:
        findings.extend(_scan_file(target))

    if args.json:
        print(
            json.dumps(
                [
                    {"path": str(p), "line": line, "phrase": phrase}
                    for p, line, phrase in findings
                ],
                indent=2,
            )
        )
    else:
        emit_grouped_path_findings(
            "validate_runtime_step_ownership",
            findings,
            render_detail=lambda line, phrase: (
                f"line {line}: banned next-step phrase: {phrase!r}"
            ),
            ok_message=f"clean across {len(existing)} rail file(s).",
        )

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
