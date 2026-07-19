#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_ROOT = ROOT / "src" / "openminion" / "modules" / "controlplane" / "channels"


def _is_fallback_context(lines: list[str], index: int) -> bool:
    for line in reversed(lines[: index + 1]):
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            return stripped.startswith("def _deliver_sync_fallback") or stripped.startswith(
                "async def _deliver_sync_fallback"
            )
    return False


def find_violations(scan_root: Path) -> list[str]:
    violations: list[str] = []
    candidates = [
        path
        for path in scan_root.rglob("*.py")
        if path.name in {"polling.py", "webhook.py"}
    ]
    for path in sorted(candidates):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            compact = line.replace(" ", "")
            if "self.deliver(payload,envelope" not in compact:
                continue
            if _is_fallback_context(lines, index):
                continue
            rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            violations.append(f"{rel}:{index + 1}: synchronous self.deliver(payload, envelope) outside _deliver_sync_fallback")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject synchronous controlplane deliver calls outside the explicit fallback."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_SCAN_ROOT)
    args = parser.parse_args(argv)
    violations = find_violations(args.root)
    if violations:
        print("[coo-09] synchronous controlplane deliver violations found:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("[coo-09] clean — no synchronous runner deliver regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
