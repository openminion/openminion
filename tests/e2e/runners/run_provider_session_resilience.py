#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
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


_PROBE_STATUS_RE = re.compile(
    r"^\[probe-status\] phase=(?P<phase>[a-z0-9_]+) exit_code=-?\d+\s*$",
    re.MULTILINE,
)
_BLOCKED_PHASES = frozenset({"config_missing", "config_env_missing", "python_missing"})
_PROVIDER_RESIDUAL_PHASES = frozenset({"turn_timeout"})


def _completed_attempt_failure(
    target: ProviderSessionTarget,
    provider_attempts: object,
) -> str:
    if not isinstance(provider_attempts, list):
        return "provider_attempts_missing"
    completed = [
        attempt
        for attempt in provider_attempts
        if isinstance(attempt, dict)
        and str(attempt.get("event_type") or "") == "llm.call.completed"
        and str(attempt.get("status") or "") == "completed"
    ]
    if not completed:
        return "completed_provider_attempts_missing"
    expected = {
        "agent_id": target.agent_id,
        "provider_name": target.provider_name,
        "service_vendor": target.service_vendor,
        "model": target.expected_model,
    }
    for attempt in completed:
        for name, value in expected.items():
            if str(attempt.get(name) or "").strip() != value:
                return f"completed_attempt_{name}_mismatch"
        if not str(attempt.get("turn_id") or "").strip():
            return "completed_attempt_turn_id_missing"
    turn_ids = {str(attempt["turn_id"]).strip() for attempt in completed}
    if len(turn_ids) < 2:
        return "distinct_completed_turns_missing"
    return ""


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
    with tempfile.TemporaryDirectory(prefix="openminion-psrc-") as temp_dir:
        summary_path = Path(temp_dir) / "summary.json"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    *probe_args,
                    "--summary-output",
                    str(summary_path),
                ],
                cwd=_ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=target.timeout_seconds + 30,
            )
        except subprocess.TimeoutExpired:
            return build_provider_session_certification_row(
                target=target,
                run_id=run_id,
                messages=messages,
                classification="provider_residual",
                failure_code="outer_timeout",
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.exists()
            else {}
        )
    latency_ms = round((time.monotonic() - started) * 1000)
    status = _PROBE_STATUS_RE.findall(result.stdout)
    phase = status[-1] if status else ""
    failure_code = phase or f"probe_exit_{result.returncode}"
    provider_attempts = summary.get("provider_attempts")
    if result.returncode == 0:
        failure_code = _completed_attempt_failure(target, provider_attempts)
        classification = "runtime_regression" if failure_code else "pass"
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
        provider_attempts=(
            list(provider_attempts) if isinstance(provider_attempts, list) else []
        ),
    )


def main(argv: list[str] | None = None) -> int:
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
