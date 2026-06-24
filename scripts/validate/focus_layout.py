#!/usr/bin/env python3.11
"""Run the generic focus-layout validators as one lint entrypoint."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import heading, item, section  # noqa: E402
from scripts.validate.focus.terminal_no_textual import main as validate_no_textual  # noqa: E402
from scripts.validate.focus.terminal_styles import main as validate_styles  # noqa: E402
from scripts.validate.focus.widget_isolation import main as validate_widget_isolation  # noqa: E402


Check = tuple[str, Callable[[list[str]], int]]

CHECKS: tuple[Check, ...] = (
    ("focus_widget_isolation", validate_widget_isolation),
    ("terminal_no_textual", validate_no_textual),
    ("terminal_styles", validate_styles),
)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    if args:
        print(
            "validate/focus_layout.py: no arguments are supported; run the "
            "individual focus validators for baseline updates.",
            file=sys.stderr,
        )
        return 2

    failures: list[str] = []
    print(heading("validate_focus_layout"))
    print("")
    print(section("Checks", kind="info"))
    for name, check in CHECKS:
        print("")
        print(item(f"running {name}", prefix="  "))
        rc = check([])
        if rc != 0:
            failures.append(name)

    if failures:
        print("", file=sys.stderr)
        print(section("Result", kind="fail", stream=sys.stderr), file=sys.stderr)
        print(item(f"failed checks: {', '.join(failures)}"), file=sys.stderr)
        return 1
    print("")
    print(section("Result", kind="ok"))
    print(item(f"clean — {len(CHECKS)} check(s) passed."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
