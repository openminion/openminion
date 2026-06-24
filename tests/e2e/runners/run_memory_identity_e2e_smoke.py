#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SmokeResult:
    name: str
    status: str  # pass, fail, skip
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class SmokeSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[SmokeResult] = field(default_factory=list)
    fixtures_ok: bool = False
    fixtures_errors: list[str] = field(default_factory=list)


def run_fixture_validation() -> tuple[bool, list[str]]:
    errors = []

    fixtures_dir = Path(__file__).resolve().parents[2] / "fixtures"

    required_fixtures = [
        fixtures_dir / "identity" / "valid_profile.yaml",
        fixtures_dir / "identity" / "degraded_profile.yaml",
        fixtures_dir / "memory" / "seeded_session.yaml",
    ]

    for fixture in required_fixtures:
        if not fixture.exists():
            errors.append(f"Missing fixture: {fixture}")

    return len(errors) == 0, errors


def run_pytest_tests(verbose: bool = False) -> list[SmokeResult]:
    results = []

    test_file = Path(__file__).resolve().parents[2] / "test_memory_identity_e2e.py"

    if not test_file.exists():
        return [
            SmokeResult(
                "test_file_exists", "fail", error=f"Test file not found: {test_file}"
            )
        ]

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-v" if verbose else "-q",
        "--tb=short",
        "-p",
        "no:warnings",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Parse pytest output
        for line in result.stdout.split("\n"):
            if "::" in line and (
                "PASSED" in line or "FAILED" in line or "SKIPPED" in line
            ):
                parts = line.split()
                test_name = parts[0] if parts else "unknown"
                if "PASSED" in line:
                    results.append(SmokeResult(test_name, "pass"))
                elif "FAILED" in line:
                    results.append(SmokeResult(test_name, "fail"))
                elif "SKIPPED" in line:
                    results.append(SmokeResult(test_name, "skip"))

        # If no specific test results but overall passed
        if not results and result.returncode == 0:
            results.append(SmokeResult("all_tests", "pass"))
        elif not results and result.returncode != 0:
            results.append(
                SmokeResult(
                    "test_suite", "fail", error=result.stderr or "Unknown error"
                )
            )

    except subprocess.TimeoutExpired:
        results.append(SmokeResult("test_suite", "fail", error="Timeout after 120s"))
    except Exception as exc:
        results.append(SmokeResult("test_suite", "fail", error=str(exc)))

    return results


def run_debug_provider_checks() -> list[SmokeResult]:
    results = []

    try:
        from openminion.cli.commands.debug import (
            OpenMinionIdentityDebugProvider,
            OpenMinionMemoryDebugProvider,
            OpenMinionRetrieveDebugProvider,
        )

        # Check identity
        try:
            provider = OpenMinionIdentityDebugProvider()
            payload = provider.get_debug()
            if payload.status.value in ("ok", "warn"):
                results.append(SmokeResult("identity_debug", "pass"))
            else:
                results.append(
                    SmokeResult(
                        "identity_debug", "fail", error=f"Status: {payload.status}"
                    )
                )
        except Exception as exc:
            results.append(SmokeResult("identity_debug", "fail", error=str(exc)))

        # Check memory
        try:
            provider = OpenMinionMemoryDebugProvider()
            payload = provider.get_debug()
            if payload.status.value in ("ok", "warn"):
                results.append(SmokeResult("memory_debug", "pass"))
            else:
                results.append(
                    SmokeResult(
                        "memory_debug", "fail", error=f"Status: {payload.status}"
                    )
                )
        except Exception as exc:
            results.append(SmokeResult("memory_debug", "fail", error=str(exc)))

        # Check retrieve
        try:
            provider = OpenMinionRetrieveDebugProvider()
            payload = provider.get_debug()
            if payload.status.value in ("ok", "warn"):
                results.append(SmokeResult("retrieve_debug", "pass"))
            else:
                results.append(
                    SmokeResult(
                        "retrieve_debug", "fail", error=f"Status: {payload.status}"
                    )
                )
        except Exception as exc:
            results.append(SmokeResult("retrieve_debug", "fail", error=str(exc)))

    except ImportError:
        results.append(
            SmokeResult("debug_providers", "skip", error="openminion not available")
        )
    except Exception as exc:
        results.append(SmokeResult("debug_providers", "fail", error=str(exc)))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="MIDE E2E Validation Smoke Script")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    summary = SmokeSummary()

    # Step 1: Fixture validation
    summary.fixtures_ok, summary.fixtures_errors = run_fixture_validation()

    # Step 2: Run tests
    pytest_results = run_pytest_tests(verbose=args.verbose)
    summary.results.extend(pytest_results)

    # Step 3: Debug provider checks
    debug_results = run_debug_provider_checks()
    summary.results.extend(debug_results)

    # Calculate totals
    summary.total = len(summary.results)
    summary.passed = sum(1 for r in summary.results if r.status == "pass")
    summary.failed = sum(1 for r in summary.results if r.status == "fail")
    summary.skipped = sum(1 for r in summary.results if r.status == "skip")

    # Output
    if args.json:
        output = {
            "mide_smoke": {
                "fixtures_ok": summary.fixtures_ok,
                "fixtures_errors": summary.fixtures_errors,
                "summary": {
                    "total": summary.total,
                    "passed": summary.passed,
                    "failed": summary.failed,
                    "skipped": summary.skipped,
                },
                "results": [
                    {
                        "name": r.name,
                        "status": r.status,
                        "duration_ms": r.duration_ms,
                        "error": r.error,
                    }
                    for r in summary.results
                ],
            }
        }
        print(json.dumps(output, indent=2))
    else:
        print("=" * 60)
        print("MIDE E2E Validation Smoke Results")
        print("=" * 60)
        print(f"Fixtures: {'OK' if summary.fixtures_ok else 'FAIL'}")
        if summary.fixtures_errors:
            for err in summary.fixtures_errors:
                print(f"  - {err}")
        print(f"\nTests: {summary.total} total")
        print(f"  Passed:  {summary.passed}")
        print(f"  Failed:  {summary.failed}")
        print(f"  Skipped: {summary.skipped}")
        print("\nDetails:")
        for r in summary.results:
            icon = {"pass": "✓", "fail": "✗", "skip": "⊘"}.get(r.status, "?")
            print(f"  {icon} {r.name}: {r.status}")
            if r.error and args.verbose:
                print(f"      Error: {r.error}")
        print("=" * 60)

    # Exit code
    if not summary.fixtures_ok:
        return 2
    if summary.failed > 0:
        return 1
    return 0
