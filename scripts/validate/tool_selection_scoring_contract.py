"""Protect tool-selection scoring from prose-driven heuristics."""

from __future__ import annotations

import argparse
import json
import re
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


_SCAN_ROOTS = (
    Path("src/openminion/services/tool"),
    Path("src/openminion/services/agent/execution"),
    Path("src/openminion/modules/brain/loop/tools"),
)


# Exact-string bans for removed shortlist/prose-scoring symbols.
_LEGACY_SYMBOL_BANS = (
    "_score_tools",
    "_ranked_selection",
    "SelectionMode.RANKED",
    'SelectionMode("ranked")',
    "SelectionMode('ranked')",
    "SelectionMode.HYBRID",  # replaced by SelectionMode.TYPED
    'SelectionMode("hybrid")',
    "SelectionMode('hybrid')",
)

# Regex bans (patterns that would bring back prose scoring).
_REGEX_BANS = (
    # User-query tokenization into sets/lists
    (
        re.compile(r"re\.findall\(\s*[r]?['\"]\\w\+['\"].*?query", re.DOTALL),
        "regex tokenization of a query variable (use typed signals, not prose)",
    ),
    (
        re.compile(r"query[_\w]*\.lower\(\)"),
        "lowercasing a query variable (prose preprocessing is banned on the shortlist path)",
    ),
    # Known legacy weight magic numbers appearing with 'score' in the same
    # line.
    (
        re.compile(r"score\s*\+=\s*2\.0"),
        "looks like the removed _score_tools name-hit weight (+2.0)",
    ),
    (
        re.compile(r"score\s*\+=\s*1\.5"),
        "looks like the removed _score_tools primary-category weight (+1.5)",
    ),
    # Retrieval/scoring algorithms reintroduced under any name
    (
        re.compile(r"\bBM25\b"),
        "BM25 scoring is not allowed on tool-selection prose paths",
    ),
    (
        re.compile(r"\bTfIdf\b|\bTF[-_]IDF\b"),
        "TF-IDF scoring is not allowed on tool-selection prose paths",
    ),
    (
        re.compile(r"cosine_similarity"),
        "cosine-similarity scoring on user prose is not allowed on tool-selection paths",
    ),
)
SKIPPED_FILENAMES = frozenset(
    {
        "test_selection_ranked_characterization.py",
        "validate/tool_selection_scoring_contract.py",
    }
)


def _scan_file(path: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    # Skip the characterization test, which is the one place allowed to
    # reference removed symbols directly.
    if path.name in SKIPPED_FILENAMES:
        return findings

    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # Skip comments and docstrings-as-lines — developers may reference
        # removed names in prose explaining the migration.
        if (
            stripped.startswith("#")
            or stripped.startswith('"""')
            or stripped.startswith("'''")
        ):
            continue

        for banned in _LEGACY_SYMBOL_BANS:
            if banned in line:
                findings.append((path, idx, f"removed symbol reference: {banned!r}"))

        for pattern, reason in _REGEX_BANS:
            if pattern.search(line):
                findings.append((path, idx, reason))

    return findings


def _iter_scan_targets(roots: tuple[Path, ...]) -> list[Path]:
    targets: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        targets.extend(sorted(root.rglob("*.py")))
    return targets


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Override the default scan roots (advanced — leave empty for CI).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit findings as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    roots = tuple(args.roots) if args.roots else _SCAN_ROOTS
    targets = _iter_scan_targets(roots)
    if not targets:
        print(heading("validate_tool_selection_scoring_contract"))
        print(status_line("warn", "no scan targets (paths missing?)"))
        return 0

    findings: list[tuple[Path, int, str]] = []
    for target in targets:
        findings.extend(_scan_file(target))

    if args.json:
        print(
            json.dumps(
                [
                    {"path": str(p), "line": line, "reason": reason}
                    for p, line, reason in findings
                ],
                indent=2,
            )
        )
    else:
        emit_grouped_path_findings(
            "validate_tool_selection_scoring_contract",
            findings,
            render_detail=lambda line, reason: f"line {line}: {reason}",
            ok_message=f"clean across {len(targets)} files in {len(roots)} scan roots.",
        )

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
