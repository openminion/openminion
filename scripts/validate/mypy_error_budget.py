#!/usr/bin/env python3.11
"""Guard the whole-tree mypy error budget against regressions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ERROR_RE = re.compile(r"^src/openminion/(?:(?P<pkg>[^/:]+)/)?.*: error:")
DEFAULT_MONTHLY_BURN_DOWN_QUOTA = {
    "api": -50,
    "cli": -50,
    "modules": -50,
    "services": -50,
    "tools": -50,
}


def _package_for(line: str) -> str:
    match = ERROR_RE.match(line)
    if not match:
        return "root"
    return match.group("pkg") or "root"


def _run_mypy(repo_root: Path) -> tuple[int, list[str]]:
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "src/openminion",
        "--explicit-package-bases",
        "--hide-error-context",
        "--no-error-summary",
        "--show-error-codes",
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout.splitlines()


def _counts(lines: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        if ": error:" not in line:
            continue
        pkg = _package_for(line)
        counts[pkg] = counts.get(pkg, 0) + 1
    return dict(sorted(counts.items()))


def _package_error_lines(lines: list[str], package: str, *, limit: int) -> list[str]:
    package_lines: list[str] = []
    for line in lines:
        if ": error:" not in line:
            continue
        if _package_for(line) == package:
            package_lines.append(line)
        if len(package_lines) >= limit:
            break
    return package_lines


def _package_file_counts(lines: list[str], package: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        if ": error:" not in line or _package_for(line) != package:
            continue
        path = line.split(":", 1)[0]
        counts[path] = counts.get(path, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Validate the whole-tree mypy error budget."
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=repo_root / "scripts" / "baselines" / "mypy_baseline.json",
    )
    parser.add_argument("--emit-baseline", action="store_true")
    args = parser.parse_args()

    returncode, lines = _run_mypy(repo_root)
    if returncode not in (0, 1):
        print("[tcr] unexpected mypy invocation failure:", file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)
        return returncode
    if returncode == 1 and not any(": error:" in line for line in lines):
        print("[tcr] mypy did not produce typed error output:", file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)
        return returncode
    current = _counts(lines)
    total = sum(current.values())

    if args.emit_baseline:
        payload = {
            "generated": "2026-05-31",
            "command": ".venv/bin/python3.11 -m mypy src/openminion --explicit-package-bases --hide-error-context --no-error-summary --show-error-codes",
            "monthly_burn_down_quota": DEFAULT_MONTHLY_BURN_DOWN_QUOTA,
            "total_errors": total,
            "package_errors": current,
        }
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(
            f"[tcr] baseline written: {args.baseline.relative_to(repo_root)} ({total} errors)"
        )
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    allowed: dict[str, int] = {
        str(pkg): int(count)
        for pkg, count in baseline.get("package_errors", {}).items()
    }
    quotas: dict[str, int] = {
        str(pkg): int(count)
        for pkg, count in baseline.get(
            "monthly_burn_down_quota", DEFAULT_MONTHLY_BURN_DOWN_QUOTA
        ).items()
    }

    regressions: list[str] = []
    for pkg in sorted(set(allowed) | set(current)):
        now = current.get(pkg, 0)
        was = allowed.get(pkg, 0)
        if now > was:
            regressions.append(f"{pkg}: {now} > baseline {was}")

    print("[tcr] mypy whole-tree ratchet")
    print(f"[tcr] current total: {total}; baseline total: {sum(allowed.values())}")
    for pkg in sorted(set(allowed) | set(current)):
        now = current.get(pkg, 0)
        was = allowed.get(pkg, 0)
        quota = quotas.get(pkg, DEFAULT_MONTHLY_BURN_DOWN_QUOTA.get(pkg, -50))
        target = max(was + quota, 0)
        headroom = was - now
        print(
            f"[tcr] {pkg}: {now} / {was} | monthly quota {quota} | next target <= {target} | headroom {headroom}"
        )

    if regressions:
        print("[tcr] regressions detected:", file=sys.stderr)
        for item in regressions:
            print(f"  {item}", file=sys.stderr)
        print("[tcr] sample regressed-package errors:", file=sys.stderr)
        for pkg in sorted(set(allowed) | set(current)):
            if current.get(pkg, 0) <= allowed.get(pkg, 0):
                continue
            print(f"  {pkg}:", file=sys.stderr)
            for line in _package_error_lines(lines, pkg, limit=25):
                print(f"    {line}", file=sys.stderr)
            print(f"  {pkg} file counts:", file=sys.stderr)
            for path, count in _package_file_counts(lines, pkg).items():
                print(f"    {path}\t{count}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
