#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import time

_ROOT = Path(__file__).resolve().parents[3]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.e2e.cli.focus.harness.provider_matrix import (  # noqa: E402
    ProviderSessionTarget,
    build_provider_session_certification_row,
    load_provider_session_resilience_manifest,
    provider_session_probe_args,
    write_provider_session_resilience_report,
)
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402


_PROBE_STATUS_RE = re.compile(
    r"^\[probe-status\] phase=(?P<phase>[a-z0-9_]+) exit_code=-?\d+\s*$",
    re.MULTILINE,
)
_BLOCKED_PHASES = frozenset({"config_missing", "config_env_missing", "python_missing"})
_PROVIDER_RESIDUAL_PHASES = frozenset({"turn_timeout"})


def _live_row(
    *,
    target: ProviderSessionTarget,
    run_id: str,
    messages: tuple[str, ...],
    required_output_marker: str,
) -> dict[str, object]:
    probe_args = provider_session_probe_args(
        target,
        run_id=run_id,
        messages=messages,
        required_output_marker=required_output_marker,
    )
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, *probe_args],
        cwd=_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    latency_ms = round((time.monotonic() - started) * 1000)
    status = _PROBE_STATUS_RE.findall(result.stdout)
    phase = status[-1] if status else ""
    failure_code = phase or f"probe_exit_{result.returncode}"
    if result.returncode == 0:
        classification = "pass"
        failure_code = ""
    elif phase in _BLOCKED_PHASES:
        classification = "blocked_external"
    elif phase in _PROVIDER_RESIDUAL_PHASES:
        classification = "provider_residual"
    elif phase and phase != "durable_turn_completed":
        classification = "runtime_regression"
    elif "probe requirement failed" in result.stderr:
        classification = "provider_residual"
        failure_code = "continuity_oracle_failed"
    else:
        classification = "runtime_regression"
    return build_provider_session_certification_row(
        target=target,
        run_id=run_id,
        messages=messages,
        classification=classification,
        failure_code=failure_code,
        latency_ms=latency_ms,
    )


def main(argv: list[str] | None = None) -> int:
    isolate_runtime_roots(prefix="openminion-provider-session-")
    parser = argparse.ArgumentParser(
        prog="run_provider_session_resilience.py",
        description="Validate provider/session resilience manifests.",
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    try:
        manifest = load_provider_session_resilience_manifest(
            manifest_path,
            root=_ROOT,
        )
        rows = None
        if not args.validate_only:
            rows = [
                _live_row(
                    target=target,
                    run_id=manifest.run_id,
                    messages=manifest.messages,
                    required_output_marker=manifest.required_output_marker,
                )
                for target in manifest.targets
            ]
        json_path, markdown_path = write_provider_session_resilience_report(
            manifest,
            manifest_path=manifest_path,
            root=_ROOT,
            validation_only=args.validate_only,
            rows=rows,
        )
    except (OSError, ValueError) as exc:
        print(f"provider session resilience manifest rejected: {exc}", file=sys.stderr)
        return 2

    print(f"{manifest.run_id}: {json_path}")
    print(f"{manifest.run_id}: {markdown_path}")
    if args.validate_only:
        return 0
    return 0 if all(row["classification"] == "pass" for row in rows or ()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
