#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_ROOTS = (
    ROOT / "src" / "openminion" / "modules" / "controlplane",
    ROOT / "tests",
)
_PATTERNS = (
    "WebhookConfig(enabled=True,secret=None",
    "WebhookConfig(enabled=True,secret=\"\"",
    "WebhookConfig(enabled=True,secret=''",
)


def _inside_config_error_assert(lines: list[str], index: int) -> bool:
    lower_window = "\n".join(lines[max(0, index - 6) : index + 1]).lower()
    return "pytest.raises(configerror" in lower_window or "assertraises(configerror" in lower_window


def find_violations(roots: tuple[Path, ...]) -> list[str]:
    violations: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                compact = line.replace(" ", "")
                if not any(pattern in compact for pattern in _PATTERNS):
                    continue
                if _inside_config_error_assert(lines, index):
                    continue
                rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                violations.append(f"{rel}:{index + 1}: enabled webhook without required secret")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject enabled Telegram webhook configs without a secret."
    )
    parser.add_argument("--root", action="append", type=Path)
    args = parser.parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_SCAN_ROOTS
    violations = find_violations(roots)
    if violations:
        print("[coo-10] webhook secret-required violations found:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("[coo-10] clean — webhook secret-required contract preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
