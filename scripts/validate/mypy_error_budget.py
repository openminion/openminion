#!/usr/bin/env python3.11
"""Guard the whole-tree mypy error budget against regressions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ERROR_RE = re.compile(r"^src/openminion/(?:(?P<pkg>[^/:]+)/)?.*: error:")
MYPY_COMMAND = (
    ".venv/bin/python3.11 -m mypy src/openminion --explicit-package-bases "
    "--hide-error-context --no-error-summary --show-error-codes"
)
HISTORICAL_FLOOR = {
    "source_commit": "0a77f9c^",
    "package_errors": {
        "api": 125,
        "cli": 576,
        "modules": 2717,
        "root": 1,
        "services": 373,
        "tools": 227,
    },
    "total_errors": 4019,
}
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


def _measure(repo_root: Path) -> tuple[dict[str, int], int, list[str]]:
    returncode, lines = _run_mypy(repo_root)
    if returncode not in (0, 1):
        print("[tcr] unexpected mypy invocation failure:", file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)
        raise SystemExit(returncode)
    if returncode == 1 and not any(": error:" in line for line in lines):
        print("[tcr] mypy did not produce typed error output:", file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)
        raise SystemExit(returncode)
    current = _counts(lines)
    return current, sum(current.values()), lines


def _read_baseline(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_errors(payload: dict[str, Any]) -> dict[str, int]:
    return {
        str(pkg): int(count)
        for pkg, count in dict(payload.get("package_errors", {}) or {}).items()
    }


def _metadata(repo_root: Path, *, counts: dict[str, int], total: int) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source_commit": _git_output(repo_root, "rev-parse", "HEAD"),
        "source_snapshot_sha256": _source_snapshot_sha256(repo_root),
        "python_version": sys.version.split()[0],
        "mypy_version": _mypy_version(repo_root),
        "config_sha256": _file_sha256(repo_root / "pyproject.toml"),
        "command": MYPY_COMMAND,
        "monthly_burn_down_quota": DEFAULT_MONTHLY_BURN_DOWN_QUOTA,
        "total_errors": total,
        "package_errors": dict(sorted(counts.items())),
        "historical_floor": HISTORICAL_FLOOR,
        "reset_debt": _reset_debt(counts, total=total),
    }


def _reset_debt(counts: dict[str, int], *, total: int) -> dict[str, Any]:
    floor = HISTORICAL_FLOOR["package_errors"]
    package_errors = {
        pkg: max(0, int(count) - int(floor.get(pkg, 0)))
        for pkg, count in sorted(counts.items())
    }
    return {
        "review_artifact": "",
        "package_errors": package_errors,
        "total_errors": max(0, total - int(HISTORICAL_FLOOR["total_errors"])),
    }


def _git_output(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _mypy_version(repo_root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--version"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip()


def _source_snapshot_sha256(repo_root: Path) -> str:
    paths = sorted((repo_root / "src" / "openminion").rglob("*.py"))
    paths.append(repo_root / "pyproject.toml")
    digest = hashlib.sha256()
    for path in paths:
        rel = path.relative_to(repo_root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _increases(
    *, current: dict[str, int], allowed: dict[str, int], current_total: int
) -> list[str]:
    regressions = []
    for pkg in sorted(set(allowed) | set(current)):
        now = current.get(pkg, 0)
        was = allowed.get(pkg, 0)
        if now > was:
            regressions.append(f"{pkg}: {now} > baseline {was}")
    if current_total > sum(allowed.values()):
        regressions.append(f"total: {current_total} > baseline {sum(allowed.values())}")
    return regressions


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        tmp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _emit_monotonic_baseline(
    *, repo_root: Path, baseline_path: Path, current: dict[str, int], total: int
) -> int:
    prior = _read_baseline(baseline_path) if baseline_path.exists() else {}
    allowed = _package_errors(prior)
    regressions = _increases(current=current, allowed=allowed, current_total=total)
    if allowed and regressions:
        print(
            "[tcr] refusing to raise mypy baseline without reviewed reset",
            file=sys.stderr,
        )
        for item in regressions:
            print(f"  {item}", file=sys.stderr)
        return 1
    payload = _metadata(repo_root, counts=current, total=total)
    prior_floor = prior.get("historical_floor")
    if isinstance(prior_floor, dict):
        payload["historical_floor"] = prior_floor
    prior_debt = prior.get("reset_debt")
    if isinstance(prior_debt, dict):
        payload["reset_debt"]["review_artifact"] = str(
            prior_debt.get("review_artifact", "") or ""
        )
    _write_json_atomic(baseline_path, payload)
    print(
        f"[tcr] baseline written: {baseline_path.relative_to(repo_root)} ({total} errors)"
    )
    return 0


def _emit_reviewed_reset(
    *,
    repo_root: Path,
    baseline_path: Path,
    review_path: Path,
    current: dict[str, int],
    total: int,
) -> int:
    review = _load_review(
        repo_root=repo_root, baseline_path=baseline_path, path=review_path
    )
    prior = _read_baseline(baseline_path)
    prior_counts = _package_errors(prior)
    proposed = dict(review["proposed_baseline"]["package_errors"])
    if prior_counts != dict(review["previous_baseline"]["package_errors"]):
        print(
            "[tcr] reviewed reset previous counts do not match active baseline",
            file=sys.stderr,
        )
        return 1
    if current != {str(k): int(v) for k, v in proposed.items()}:
        print(
            "[tcr] reviewed reset proposed counts do not match current measurement",
            file=sys.stderr,
        )
        return 1
    if int(review["proposed_baseline"]["total_errors"]) != total:
        print(
            "[tcr] reviewed reset proposed total does not match current measurement",
            file=sys.stderr,
        )
        return 1
    payload = _metadata(repo_root, counts=current, total=total)
    payload["historical_floor"] = prior.get("historical_floor", HISTORICAL_FLOOR)
    resolved_review_path = (
        review_path if review_path.is_absolute() else repo_root / review_path
    ).resolve(strict=False)
    payload["reset_debt"]["review_artifact"] = resolved_review_path.relative_to(
        repo_root
    ).as_posix()
    _write_json_atomic(baseline_path, payload)
    print(
        f"[tcr] reviewed reset written: {baseline_path.relative_to(repo_root)} ({total} errors)"
    )
    return 0


def _load_review(*, repo_root: Path, baseline_path: Path, path: Path) -> dict[str, Any]:
    canonical_root = baseline_path.parent / "mypy_resets"
    resolved = path if path.is_absolute() else repo_root / path
    resolved = resolved.resolve(strict=False)
    if not resolved.is_relative_to(canonical_root.resolve(strict=False)):
        raise SystemExit(
            "[tcr] reviewed reset must live under scripts/baselines/mypy_resets"
        )
    if not resolved.exists():
        raise SystemExit("[tcr] reviewed reset artifact is missing")
    if (
        _git_output(
            repo_root,
            "ls-files",
            "--error-unmatch",
            str(resolved.relative_to(repo_root)),
        )
        == ""
    ):
        raise SystemExit("[tcr] reviewed reset artifact must be tracked by git")
    review = json.loads(resolved.read_text(encoding="utf-8"))
    required = [
        "schema_version",
        "review_id",
        "created_at",
        "source_commit",
        "source_snapshot_sha256",
        "python_version",
        "mypy_version",
        "config_sha256",
        "command",
        "owner",
        "approved_by",
        "approval_reference",
        "reason",
        "recovery_tracker",
        "expires_at",
        "previous_baseline",
        "proposed_baseline",
    ]
    missing = [key for key in required if not review.get(key)]
    if missing:
        raise SystemExit(
            f"[tcr] reviewed reset is missing required fields: {', '.join(missing)}"
        )
    if int(review["schema_version"]) != 1:
        raise SystemExit("[tcr] reviewed reset schema_version must be 1")
    _parse_review_time(review["created_at"], field="created_at")
    expires_at = datetime.fromisoformat(
        str(review["expires_at"]).replace("Z", "+00:00")
    )
    if expires_at <= datetime.now(UTC):
        raise SystemExit("[tcr] reviewed reset is expired")
    _assert_count_block(review["previous_baseline"], field="previous_baseline")
    _assert_count_block(review["proposed_baseline"], field="proposed_baseline")
    current_meta = _metadata(repo_root, counts={}, total=0)
    for key in (
        "source_commit",
        "source_snapshot_sha256",
        "python_version",
        "mypy_version",
        "config_sha256",
        "command",
    ):
        if str(review.get(key) or "") != str(current_meta.get(key) or ""):
            raise SystemExit(
                f"[tcr] reviewed reset {key} does not match current environment"
            )
    return review


def _parse_review_time(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"[tcr] reviewed reset {field} is not valid ISO time") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"[tcr] reviewed reset {field} must include timezone")
    return parsed


def _assert_count_block(value: object, *, field: str) -> None:
    if not isinstance(value, dict):
        raise SystemExit(f"[tcr] reviewed reset {field} must be an object")
    package_errors = value.get("package_errors")
    if not isinstance(package_errors, dict) or not package_errors:
        raise SystemExit(f"[tcr] reviewed reset {field}.package_errors is required")
    try:
        total = int(value["total_errors"])
        package_total = sum(int(count) for count in package_errors.values())
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            f"[tcr] reviewed reset {field} counts must be integers"
        ) from exc
    if total != package_total:
        raise SystemExit(
            f"[tcr] reviewed reset {field}.total_errors does not equal package sum"
        )


def _package_error_lines(lines: list[str], package: str, *, limit: int) -> list[str]:
    package_lines: list[str] = []
    for line in lines:
        if ": error:" not in line or _package_for(line) != package:
            continue
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


def _print_report(
    *, current: dict[str, int], total: int, baseline: dict[str, Any], lines: list[str]
) -> int:
    allowed = _package_errors(baseline)
    quotas = {
        str(pkg): int(count)
        for pkg, count in baseline.get(
            "monthly_burn_down_quota", DEFAULT_MONTHLY_BURN_DOWN_QUOTA
        ).items()
    }
    print("[tcr] mypy whole-tree ratchet")
    print(f"[tcr] current total: {total}; baseline total: {sum(allowed.values())}")
    floor = baseline.get("historical_floor", HISTORICAL_FLOOR)
    debt = baseline.get("reset_debt", {})
    print(f"[tcr] historical floor total: {floor.get('total_errors', 0)}")
    print(f"[tcr] reset debt total: {debt.get('total_errors', 0)}")
    for pkg in sorted(set(allowed) | set(current)):
        now = current.get(pkg, 0)
        was = allowed.get(pkg, 0)
        quota = quotas.get(pkg, DEFAULT_MONTHLY_BURN_DOWN_QUOTA.get(pkg, -50))
        target = max(was + quota, 0)
        headroom = was - now
        print(
            f"[tcr] {pkg}: {now} / {was} | monthly quota {quota} | next target <= {target} | headroom {headroom}"
        )
    regressions = _increases(current=current, allowed=allowed, current_total=total)
    if not regressions:
        return 0
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
    parser.add_argument("--emit-reviewed-reset", type=Path)
    args = parser.parse_args()

    current, total, lines = _measure(repo_root)
    baseline_path = (
        args.baseline if args.baseline.is_absolute() else repo_root / args.baseline
    )
    if args.emit_reviewed_reset is not None:
        return _emit_reviewed_reset(
            repo_root=repo_root,
            baseline_path=baseline_path,
            review_path=args.emit_reviewed_reset,
            current=current,
            total=total,
        )
    if args.emit_baseline:
        return _emit_monotonic_baseline(
            repo_root=repo_root,
            baseline_path=baseline_path,
            current=current,
            total=total,
        )
    return _print_report(
        current=current,
        total=total,
        baseline=_read_baseline(baseline_path),
        lines=lines,
    )


if __name__ == "__main__":
    raise SystemExit(main())
