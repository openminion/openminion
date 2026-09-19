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

from openminion.base.redaction import redact_mapping  # noqa: E402

from tests.e2e.cli.focus.harness.provider_matrix import (  # noqa: E402
    CertificationClassification,
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
_PROVIDER_FAILURE_CODES = frozenset({"AUTH_ERROR", "RATE_LIMITED", "TIMEOUT"})
_PROVIDER_ATTEMPT_FIELDS = frozenset(
    {
        "event_type",
        "llm_call_id",
        "provider_name",
        "service_vendor",
        "model",
        "status",
        "error_code",
        "retry_eligible",
        "provider_round_trip_ms",
        "invocation_id",
        "execution_id",
        "agent_id",
        "trace_key",
        "session_id",
        "turn_id",
    }
)


def _completed_attempt_failure(
    target: ProviderSessionTarget,
    provider_attempts: object,
    session_id: str,
) -> str:
    if not isinstance(provider_attempts, list):
        return "provider_attempts_missing"
    if any(not isinstance(attempt, dict) for attempt in provider_attempts):
        return "provider_attempts_malformed"
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
        "session_id": session_id,
    }
    for attempt in completed:
        for name, value in expected.items():
            if attempt.get(name) != value:
                return f"completed_attempt_{name}_mismatch"
        if (
            not isinstance(attempt.get("turn_id"), str)
            or not attempt["turn_id"].strip()
        ):
            return "completed_attempt_turn_id_missing"
    turn_ids = {str(attempt["turn_id"]).strip() for attempt in completed}
    if len(turn_ids) < 2:
        return "distinct_completed_turns_missing"
    return ""


def _probe_classification(
    *,
    target: ProviderSessionTarget,
    session_id: str,
    returncode: int,
    phase: str,
    provider_attempts: object,
    requirement_failed: bool,
) -> tuple[CertificationClassification, str]:
    if phase in _BLOCKED_PHASES:
        return "blocked_external", phase
    if returncode == 0:
        failure = _completed_attempt_failure(target, provider_attempts, session_id)
        return ("inconclusive", failure) if failure else ("pass", "")
    if not isinstance(provider_attempts, list) or any(
        not isinstance(attempt, dict) for attempt in provider_attempts
    ):
        return "inconclusive", "provider_attempts_missing_or_malformed"
    terminal_attempts = [
        attempt
        for attempt in provider_attempts
        if attempt.get("event_type") in ("llm.call.completed", "llm.call.failed")
    ]
    if terminal_attempts:
        attempt = terminal_attempts[-1]
        expected = {
            "agent_id": target.agent_id,
            "provider_name": target.provider_name,
            "service_vendor": target.service_vendor,
            "model": target.expected_model,
            "session_id": session_id,
        }
        if (
            attempt.get("event_type") == "llm.call.failed"
            and attempt.get("status") == "failed"
            and all(attempt.get(key) == value for key, value in expected.items())
            and isinstance(attempt.get("turn_id"), str)
            and attempt["turn_id"].strip()
            and isinstance(attempt.get("llm_call_id"), str)
            and attempt["llm_call_id"].strip()
        ):
            code = str(attempt.get("error_code") or "")
            if code in _PROVIDER_FAILURE_CODES:
                return "provider_residual", code
    if requirement_failed:
        return "inconclusive", "continuity_oracle_failed"
    return "inconclusive", phase or f"probe_exit_{returncode}"


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
    summary: object = {}
    report_failure = ""
    returncode: int | None = None
    phase = "outer_timeout"
    requirement_failed = False
    classification: CertificationClassification = "inconclusive"
    failure_code = phase
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
            pass
        else:
            returncode = result.returncode
            status = _PROBE_STATUS_RE.findall(result.stdout)
            phase = status[-1] if status else ""
            requirement_failed = "probe requirement failed" in result.stderr
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            report_failure = "probe_report_missing"
        except (OSError, ValueError):
            report_failure = "probe_report_malformed"
        if not isinstance(summary, dict):
            report_failure = "probe_report_malformed"
        if returncode is not None:
            if report_failure and phase not in _BLOCKED_PHASES:
                failure_code = report_failure
            else:
                classification, failure_code = _probe_classification(
                    target=target,
                    session_id=f"{run_id}:{target.provider_class}",
                    returncode=returncode,
                    phase=phase,
                    provider_attempts=summary.get("provider_attempts")
                    if isinstance(summary, dict)
                    else None,
                    requirement_failed=requirement_failed,
                )
    provider_attempts = (
        summary.get("provider_attempts") if isinstance(summary, dict) else None
    )
    safe_attempts = []
    for attempt in provider_attempts if isinstance(provider_attempts, list) else ():
        if not isinstance(attempt, dict):
            continue
        facts = {
            key: value
            for key, value in attempt.items()
            if key in _PROVIDER_ATTEMPT_FIELDS
            and (value is None or isinstance(value, (str, int, float, bool)))
        }
        for key in ("usage", "cost"):
            values = attempt.get(key)
            if isinstance(values, dict):
                facts[key] = {
                    name: value
                    for name, value in values.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                }
        safe_attempts.append(redact_mapping(facts)[0])
    row = build_provider_session_certification_row(
        target=target,
        run_id=run_id,
        messages=messages,
        classification=classification,
        failure_code=failure_code,
        latency_ms=round((time.monotonic() - started) * 1000),
        provider_attempts=safe_attempts,
    )
    row.update(
        probe_exit_code=returncode,
        probe_phase=phase,
        probe_requirement_failed=requirement_failed,
        probe_report_failure=report_failure,
    )
    return row


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
