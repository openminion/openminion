from __future__ import annotations

import os
from pathlib import Path

BANNED_SYMBOLS = (
    "RuntimeClarificationGuardManager",
    "RUNTIME_CLARIFICATION_POLICY_REGISTRY",
    "ToolClarificationPolicy",
    "RuntimeClarificationGuardDecision",
    "RUNTIME_CLARIFICATION_GUARD_SOURCE",
    "first_question",
    "richer_question",
    "evaluate_clarify_answer",
)

# Patterns that are allowed (compat shim comment, this test file, docs)
ALLOWED_PATHS = {
    "tests/test_rcl11_reintroduction_guard.py",
}

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "openminion"


def test_no_banned_runtime_clarification_symbols() -> None:
    violations: list[str] = []

    for root, _dirs, files in os.walk(SRC_ROOT):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            filepath = Path(root) / filename
            rel = str(filepath.relative_to(SRC_ROOT.parent.parent))
            if any(rel.endswith(allowed) for allowed in ALLOWED_PATHS):
                continue

            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue

            for symbol in BANNED_SYMBOLS:
                # Skip if it's in a comment with "DEPRECATED" or "retired" or "removed"
                for i, line in enumerate(content.splitlines(), 1):
                    if symbol in line:
                        lower_line = line.lower()
                        if any(
                            w in lower_line
                            for w in (
                                "deprecated",
                                "retired",
                                "removed",
                                "compat shim",
                                "rcl-11",
                            )
                        ):
                            continue
                        violations.append(f"{rel}:{i}: found banned symbol '{symbol}'")

    assert not violations, (
        "Banned runtime clarification guard symbols found in src/openminion/. "
        "These were removed in RCL-11 (2026-03-25). Violations:\n"
        + "\n".join(violations)
    )
