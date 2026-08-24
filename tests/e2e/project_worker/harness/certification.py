from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Literal, cast

from openminion.base.generated_paths import resolve_generated_root

RUN_SCHEMA_VERSION = "sustained-autonomy-certification-run.v1"
REPORT_SCHEMA_VERSION = "sustained-autonomy-certification-report.v1"
REPORT_DIRNAME = "sustained-autonomy-certification"
MINIMUM_ELAPSED_SECONDS = {
    "research-2h-interim": 7_200,
    "research-8h": 28_800,
    "code-24h": 86_400,
}
INTERIM_PILOT_KINDS = frozenset({"research-2h-interim"})
FULL_RECOVERY_CHECKS = frozenset(
    {"restart", "reconnect", "interruption", "scheduled_wake"}
)

PilotKind = Literal["research-2h-interim", "research-8h", "code-24h"]


@dataclass(frozen=True)
class CertificationManifest:
    run_id: str
    pilot_kind: PilotKind
    approved_start_utc: str
    approved_end_utc: str
    minimum_elapsed_seconds: int
    workspace_path: str
    workspace_revision: str
    runtime_python: str
    runtime_source_root: str
    runtime_source_revision: str
    home_root: str
    data_root: str
    generated_root: str
    session_id: str
    permission_profile: str
    verification_domain: str
    provider_config_ref: str
    provider_config_sha256: str
    agent_id: str
    model: str
    profile: str
    goal_file: str
    goal_sha256: str
    execution_command: str
    evidence_file: str
    cycle_interval_seconds: int
    monitor_interval_seconds: int
    recovery_window_seconds: int
    daemon_restart_offset_seconds: int
    slo: dict[str, Any]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_digest(value: object, label: str) -> str:
    digest = str(value or "").strip()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _resolve_file(value: object, workspace: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("required file path is missing")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else workspace / path


def _require_file_hash(path: Path, digest: object, label: str) -> None:
    expected = _require_digest(digest, f"{label} SHA-256")
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"{label} does not match its approved SHA-256")


def _require_runtime_import(
    *,
    runtime_python: Path,
    runtime_source_root: Path,
) -> None:
    if not runtime_python.is_file():
        raise ValueError("runtime_python is unavailable")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime_source_root / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            str(runtime_python),
            "-c",
            "import openminion; print(openminion.__file__)",
        ],
        cwd=runtime_source_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    expected_root = (runtime_source_root / "src").resolve()
    imported = Path(result.stdout.strip()).resolve(strict=False)
    if result.returncode != 0 or not imported.is_relative_to(expected_root):
        raise ValueError("runtime_python does not import the approved source root")


def _validate_live_slo(slo: dict[str, Any]) -> None:
    verifier_command = _required_str(slo, "verifier_command")
    verifier_hash = _require_digest(
        slo.get("verifier_command_sha256"),
        "verifier command SHA-256",
    )
    if _sha256_text(verifier_command) != verifier_hash:
        raise ValueError("verifier command SHA-256 mismatch")
    artifact_contract = _required_str(slo, "expected_artifact_contract")
    artifact_hash = _require_digest(
        slo.get("expected_artifact_contract_sha256"),
        "artifact contract SHA-256",
    )
    if _sha256_text(artifact_contract) != artifact_hash:
        raise ValueError("artifact contract SHA-256 mismatch")

    enforced = _require_mapping_keys(
        slo,
        "enforced_limits",
        ("max_iterations", "turn_timeout_seconds", "verification_timeout_seconds"),
    )
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in enforced.values()
    ):
        raise ValueError("enforced limits must be positive integers")
    observed = _require_mapping_keys(
        slo,
        "observed_limits",
        (
            "token",
            "cost",
            "retry",
            "tool_call",
            "storage",
            "cost_provenance",
            "side_effect_measurement",
            "side_effect_limit",
        ),
    )
    for key in ("token", "cost", "retry", "tool_call", "storage"):
        value = observed[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise ValueError("observed limits must be finite nonnegative numbers")
    if observed["cost_provenance"] not in {
        "provider_only",
        "provider_or_estimated",
    }:
        raise ValueError("unsupported cost_provenance")
    if (
        observed["side_effect_measurement"] != "not_supported"
        or observed["side_effect_limit"] is not None
    ):
        raise ValueError("side-effect measurement must remain unsupported")
    operator = _require_mapping_keys(
        slo,
        "operator_limits",
        ("provider_spend_cap", "isolated_workspace", "remote_write_allowed"),
    )
    if (
        not str(operator["provider_spend_cap"] or "").strip()
        or operator["isolated_workspace"] is not True
        or operator["remote_write_allowed"] is not False
    ):
        raise ValueError("operator limits do not authorize an isolated pilot")
    planned = slo.get("planned_approval_points")
    if not isinstance(planned, list) or not all(
        isinstance(item, str) and item.strip() for item in planned
    ):
        raise ValueError("planned_approval_points must be a non-empty string list")


def _require_source_revision(workspace: Path, source_revision: str) -> None:
    revision = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if revision.returncode != 0:
        raise ValueError("approved workspace is not a Git worktree")
    if revision.stdout.strip() != source_revision:
        raise ValueError("approved workspace revision does not match source_revision")


def _require_clean_revision(workspace: Path, source_revision: str) -> None:
    _require_source_revision(workspace, source_revision)
    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise ValueError("approved Git worktree must be clean")


def validate_certification_manifest(
    path: Path,
    *,
    now: datetime | None = None,
    for_live_run: bool = False,
) -> CertificationManifest:
    payload = _read_json(path)
    if payload.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ValueError("unsupported sustained autonomy manifest schema_version")
    if payload.get("compressed_fixture") or payload.get("compressed"):
        raise ValueError("compressed fixtures cannot be certification manifests")

    pilot_kind = _required_str(payload, "pilot_kind")
    if pilot_kind not in MINIMUM_ELAPSED_SECONDS:
        raise ValueError(
            "pilot_kind must be research-2h-interim, research-8h, or code-24h"
        )
    minimum = _required_int(payload, "minimum_elapsed_seconds")
    if minimum < MINIMUM_ELAPSED_SECONDS[pilot_kind]:
        raise ValueError("minimum_elapsed_seconds is below the pilot minimum")

    start = _parse_utc(_required_str(payload, "approved_start_utc"))
    end = _parse_utc(_required_str(payload, "approved_end_utc"))
    if (end - start).total_seconds() < minimum:
        raise ValueError("approved window is shorter than minimum elapsed seconds")
    if _parse_utc(_required_str(payload, "approval_expires_utc")) <= (
        now or datetime.now(UTC)
    ):
        raise ValueError("manifest approval has expired")

    workspace = Path(_required_str(payload, "workspace_path")).expanduser()
    approved_workspace = Path(
        _required_str(payload, "approved_workspace_path")
    ).expanduser()
    if workspace.resolve(strict=False) != approved_workspace.resolve(strict=False):
        raise ValueError("workspace_path does not match approved_workspace_path")
    if not workspace.exists():
        raise ValueError("approved workspace does not exist")
    workspace_revision = str(
        payload.get("workspace_revision") or payload.get("source_revision") or ""
    ).strip()
    if not workspace_revision:
        raise ValueError("missing required field: workspace_revision")

    runtime_python = str(payload.get("runtime_python") or "").strip()
    runtime_source_root = str(payload.get("runtime_source_root") or "").strip()
    runtime_source_revision = str(payload.get("runtime_source_revision") or "").strip()
    home_root = str(payload.get("home_root") or "").strip()
    data_root = str(payload.get("data_root") or "").strip()
    generated_root = str(payload.get("generated_root") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    permission_profile = str(payload.get("permission_profile") or "").strip()
    verification_domain = str(payload.get("verification_domain") or "").strip()
    if for_live_run:
        required_live = {
            "runtime_python": runtime_python,
            "runtime_source_root": runtime_source_root,
            "runtime_source_revision": runtime_source_revision,
            "home_root": home_root,
            "data_root": data_root,
            "generated_root": generated_root,
            "session_id": session_id,
            "permission_profile": permission_profile,
            "verification_domain": verification_domain,
        }
        missing_live = [name for name, value in required_live.items() if not value]
        if missing_live:
            raise ValueError(
                "live certification is missing: " + ", ".join(missing_live)
            )
        for name in (
            runtime_python,
            runtime_source_root,
            home_root,
            data_root,
            generated_root,
        ):
            if not Path(name).expanduser().is_absolute():
                raise ValueError("live runtime and root paths must be absolute")
        _require_clean_revision(workspace, workspace_revision)
        _require_clean_revision(
            Path(runtime_source_root).expanduser(),
            runtime_source_revision,
        )
        _require_runtime_import(
            runtime_python=Path(runtime_python),
            runtime_source_root=Path(runtime_source_root),
        )

    required_slo = (
        "verifier_command",
        "verifier_criteria",
        "recovery_checks",
        "side_effect_scope",
    )
    slo = _require_mapping_keys(payload, "slo", required_slo)
    execution_command = str(slo.get("execution_command") or "").strip()
    evidence_file = str(slo.get("evidence_file") or "").strip()
    if for_live_run and (execution_command or evidence_file):
        raise ValueError("live certification rejects caller command and evidence paths")
    if for_live_run:
        _validate_live_slo(slo)
    recovery_checks = slo.get("recovery_checks")
    if not isinstance(recovery_checks, list) or not all(
        isinstance(item, str) and item.strip() for item in recovery_checks
    ):
        raise ValueError("slo.recovery_checks must be a non-empty string list")
    if pilot_kind not in INTERIM_PILOT_KINDS and not FULL_RECOVERY_CHECKS.issubset(
        {item.strip() for item in recovery_checks}
    ):
        raise ValueError(
            "full pilots require restart, reconnect, interruption, and scheduled_wake checks"
        )

    provider = _require_mapping_keys(
        payload,
        "provider",
        ("config_ref", "agent_id", "model", "profile"),
    )
    provider_config_sha256 = str(provider.get("config_sha256") or "").strip()
    goal_sha256 = str(payload.get("goal_sha256") or "").strip()
    cycle_interval_seconds = int(payload.get("cycle_interval_seconds") or 0)
    monitor_interval_seconds = int(payload.get("monitor_interval_seconds") or 0)
    recovery_window_seconds = int(payload.get("recovery_window_seconds") or 0)
    daemon_restart_offset_seconds = int(
        payload.get("daemon_restart_offset_seconds") or 0
    )
    if for_live_run:
        provider_path = _resolve_file(provider["config_ref"], workspace)
        goal_path = _resolve_file(payload.get("goal_file"), workspace)
        _require_file_hash(provider_path, provider_config_sha256, "provider config")
        _require_file_hash(goal_path, goal_sha256, "goal")
        if not 2 <= cycle_interval_seconds <= 3600:
            raise ValueError("cycle_interval_seconds must be in 2..3600")
        if not 1 <= monitor_interval_seconds < cycle_interval_seconds:
            raise ValueError("monitor_interval_seconds must be below cycle interval")
        if recovery_window_seconds <= 0 or daemon_restart_offset_seconds < 0:
            raise ValueError("recovery timing values must be positive")
        enforced = slo["enforced_limits"]
        required_recovery = (
            cycle_interval_seconds
            + int(enforced["turn_timeout_seconds"])
            + int(enforced["verification_timeout_seconds"])
            + monitor_interval_seconds
        )
        if recovery_window_seconds < required_recovery:
            raise ValueError("recovery_window_seconds cannot cover the next wake")
        if daemon_restart_offset_seconds + 30 + recovery_window_seconds > minimum:
            raise ValueError("recovery restart cannot fit inside the pilot")
    if _has_secret_bearing_field(payload):
        raise ValueError("manifest contains a secret-bearing field")

    return CertificationManifest(
        run_id=_required_str(payload, "run_id"),
        pilot_kind=cast(PilotKind, pilot_kind),
        approved_start_utc=_utc_text(start),
        approved_end_utc=_utc_text(end),
        minimum_elapsed_seconds=minimum,
        workspace_path=str(workspace.resolve(strict=False)),
        workspace_revision=workspace_revision,
        runtime_python=runtime_python,
        runtime_source_root=runtime_source_root,
        runtime_source_revision=runtime_source_revision,
        home_root=home_root,
        data_root=data_root,
        generated_root=generated_root,
        session_id=session_id,
        permission_profile=permission_profile,
        verification_domain=verification_domain,
        provider_config_ref=str(provider["config_ref"]),
        provider_config_sha256=provider_config_sha256,
        agent_id=str(provider["agent_id"]),
        model=str(provider["model"]),
        profile=str(provider["profile"]),
        goal_file=_required_str(payload, "goal_file"),
        goal_sha256=goal_sha256,
        execution_command=execution_command,
        evidence_file=evidence_file,
        cycle_interval_seconds=cycle_interval_seconds,
        monitor_interval_seconds=monitor_interval_seconds,
        recovery_window_seconds=recovery_window_seconds,
        daemon_restart_offset_seconds=daemon_restart_offset_seconds,
        slo=slo,
    )


def write_certification_report(
    manifest: CertificationManifest,
    *,
    manifest_path: Path,
    root: Path,
    validation_only: bool,
    evidence: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    output_root = (
        resolve_generated_root(root) / REPORT_DIRNAME / _safe_segment(manifest.run_id)
    )
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "generated_at_utc": _utc_text(datetime.now(UTC)),
        "validation_only": validation_only,
        "outcome": (
            "blocked_external"
            if validation_only
            else str((evidence or {}).get("outcome") or "in_progress")
        ),
        "certification_level": (
            "interim_support"
            if manifest.pilot_kind in INTERIM_PILOT_KINDS
            else "full_certification"
        ),
        "manifest_path": str(manifest_path.resolve(strict=False)),
        "manifest": asdict(manifest),
        "redaction_status": "redacted",
        "raw_provider_response_ref": None,
        "evidence": evidence,
    }
    json_path = output_root / "certification-report.json"
    markdown_path = output_root / "certification-report.md"
    json_temp = json_path.with_suffix(".json.tmp")
    markdown_temp = markdown_path.with_suffix(".md.tmp")
    json_temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_temp.write_text(_render_report_markdown(payload), encoding="utf-8")
    json_temp.replace(json_path)
    markdown_temp.replace(markdown_path)
    return json_path, markdown_path


def _runtime_command_prefix(manifest: CertificationManifest) -> list[str]:
    return [
        manifest.runtime_python,
        "-m",
        "openminion",
        "--config",
        str(_resolve_file(manifest.provider_config_ref, Path(manifest.workspace_path))),
        "--home-root",
        manifest.home_root,
        "--data-root",
        manifest.data_root,
        "--generated-root",
        manifest.generated_root,
    ]


def _runtime_environment(manifest: CertificationManifest) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(manifest.runtime_source_root) / "src")
    return env


def _run_runtime_command(
    manifest: CertificationManifest,
    *arguments: str,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [*_runtime_command_prefix(manifest), *arguments]
    try:
        return subprocess.run(
            command,
            cwd=manifest.runtime_source_root,
            env=_runtime_environment(manifest),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
        )
    except KeyboardInterrupt:
        return subprocess.CompletedProcess(command, 130, stdout="", stderr="")


def _json_output(
    result: subprocess.CompletedProcess[str], *, code: str
) -> dict[str, Any]:
    if result.returncode != 0:
        raise ValueError(code)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(code) from exc
    if not isinstance(payload, dict):
        raise ValueError(code)
    return payload


def _measure_storage(*roots: str) -> int:
    total = 0
    seen: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root).resolve(strict=False)
        if root in seen or not root.exists():
            continue
        seen.add(root)
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    return total


def _checkpoint_metrics(
    manifest: CertificationManifest,
    *,
    autonomy_run: dict[str, Any],
) -> tuple[dict[str, int | float], str, str]:
    from argparse import Namespace

    from openminion.cli.commands.autonomy_project import (
        configured_cron_store,
        project_task_manager,
    )
    from openminion.modules.task import load_latest_project_checkpoint
    from openminion.modules.telemetry.usage import StatsService

    task_id = str(autonomy_run.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("SAC_EVIDENCE_MISSING")
    args = Namespace(
        config=str(
            _resolve_file(manifest.provider_config_ref, Path(manifest.workspace_path))
        ),
        home_root=manifest.home_root,
        data_root=manifest.data_root,
        generated_root=manifest.generated_root,
        task_db="",
    )
    manager = project_task_manager(args)
    checkpoint = load_latest_project_checkpoint(manager, task_id=task_id)
    if checkpoint is None:
        raise ValueError("SAC_EVIDENCE_MISSING")
    gateway_run_id = str(checkpoint.payload.get("gateway_run_id") or "").strip()
    if not gateway_run_id:
        raise ValueError("SAC_METRIC_MISSING")
    store = configured_cron_store(args, config_ref=args.config)
    try:
        stats = StatsService(store)
        usage = stats.get_run_token_usage(
            gateway_run_id,
            session_id=manifest.session_id,
        )
        turn_cost = stats.get_run_turn_cost(
            gateway_run_id,
            session_id=manifest.session_id,
        )
    finally:
        store.close()
    provenance = manifest.slo["observed_limits"]["cost_provenance"]
    if usage is None or usage.records_emitted <= 0:
        raise ValueError("SAC_METRIC_MISSING")
    if provenance == "provider_only":
        if not usage.has_provider_cost:
            raise ValueError("SAC_METRIC_MISSING")
        cost = usage.total_provider_cost_usd
    else:
        if not usage.has_provider_cost and not usage.has_estimated_cost:
            raise ValueError("SAC_METRIC_MISSING")
        cost = usage.total_provider_cost_usd + usage.total_estimated_cost_usd
    if turn_cost is None or turn_cost.provider_calls_total < 1:
        raise ValueError("SAC_METRIC_MISSING")
    metrics: dict[str, int | float] = {
        "token": usage.total_input_tokens + usage.total_output_tokens,
        "cost": cost,
        "retry": turn_cost.provider_retries,
        "tool_call": turn_cost.invoked_tool_count,
        "iteration": checkpoint.project_run.committed_cycle_count,
        "storage": _measure_storage(
            manifest.workspace_path,
            manifest.generated_root,
        ),
    }
    return metrics, checkpoint.checkpoint_id, gateway_run_id


def _observed_limit_reached(
    metrics: dict[str, int | float],
    limits: dict[str, Any],
) -> bool:
    return any(
        float(metrics[key]) >= float(limits[key]) for key in limits if key in metrics
    )


def _cancel_run(manifest: CertificationManifest, run_id: str) -> str | None:
    cancelled = _run_runtime_command(
        manifest,
        "autonomy",
        "cancel",
        run_id,
        "--json",
        timeout=30,
    )
    if cancelled.returncode != 0:
        return "SAC_CANCEL_FAILED"
    shown = _run_runtime_command(
        manifest,
        "autonomy",
        "show",
        run_id,
        "--json",
        timeout=30,
    )
    if shown.returncode != 0:
        return "SAC_CANCEL_FAILED"
    payload = _json_output(shown, code="SAC_CANCEL_FAILED")
    run = payload.get("run")
    if not isinstance(run, dict) or run.get("status") != "cancelled":
        return "SAC_CANCEL_FAILED"

    from argparse import Namespace

    from openminion.cli.commands.autonomy_project import (
        configured_cron_store,
        project_task_manager,
    )

    task_id = str(run.get("task_id") or "").strip()
    if not task_id:
        return "SAC_CANCEL_FAILED"
    args = Namespace(
        config=str(
            _resolve_file(manifest.provider_config_ref, Path(manifest.workspace_path))
        ),
        home_root=manifest.home_root,
        data_root=manifest.data_root,
        generated_root=manifest.generated_root,
        task_db="",
    )
    task = project_task_manager(args).get_task(task_id)
    linked_job_id = str(
        (task.metadata if task else {}).get("linked_cron_job_id") or ""
    ).strip()
    if not linked_job_id:
        return "SAC_CANCEL_FAILED"
    store = configured_cron_store(args, config_ref=args.config)
    try:
        return (
            None
            if store.get_cron_job(linked_job_id) is None
            else "SAC_CRON_JOB_REMAINS"
        )
    finally:
        store.close()


def _fail_live_run(
    manifest: CertificationManifest,
    *,
    run_id: str,
    manifest_path: Path,
    root: Path,
    started: datetime,
    metrics: dict[str, int | float],
    recovery_events: list[dict[str, str]],
    error_code: str,
) -> tuple[int, Path, Path]:
    cancel_error = _cancel_run(manifest, run_id)
    return _write_live_result(
        manifest,
        manifest_path=manifest_path,
        root=root,
        outcome="failed_certification",
        started=started,
        metrics=metrics,
        recovery_events=recovery_events,
        error_code=cancel_error or error_code,
    )


def _write_live_result(
    manifest: CertificationManifest,
    *,
    manifest_path: Path,
    root: Path,
    outcome: str,
    started: datetime,
    metrics: dict[str, int | float],
    recovery_events: list[dict[str, str]],
    error_code: str | None = None,
    verifier_passed: bool = False,
) -> tuple[int, Path, Path]:
    ended = datetime.now(UTC)
    evidence = {
        "outcome": outcome,
        "started_at_utc": _utc_text(started),
        "ended_at_utc": _utc_text(ended),
        "metrics": {
            **metrics,
            "elapsed_seconds": (ended - started).total_seconds(),
            "wall_clock": (ended - started).total_seconds(),
        },
        "recovery_events": recovery_events,
        "verifier_passed": verifier_passed,
        "memory_help": "not_applicable:no memory-help claim made",
        "side_effect_measurement": "not_supported",
        "side_effect_limit": None,
        "side_effect_count": None,
        "effect_dedup_status": "not_supported",
        "duplicate_effect_count": None,
        "error": (
            {
                "code": error_code,
                "message": "Sustained autonomy certification stopped.",
                "details": {},
            }
            if error_code
            else None
        ),
    }
    paths = write_certification_report(
        manifest,
        manifest_path=manifest_path,
        root=root,
        validation_only=False,
        evidence=evidence,
    )
    return (0 if outcome == "pass" else 1), *paths


def run_certification(
    manifest: CertificationManifest,
    *,
    manifest_path: Path,
    root: Path,
) -> tuple[int, Path, Path]:
    _require_clean_revision(
        Path(manifest.workspace_path),
        manifest.workspace_revision,
    )
    _require_clean_revision(
        Path(manifest.runtime_source_root),
        manifest.runtime_source_revision,
    )
    current = datetime.now(UTC)
    start = _parse_utc(manifest.approved_start_utc)
    end = _parse_utc(manifest.approved_end_utc)
    if (
        current < start
        or current + timedelta(seconds=manifest.minimum_elapsed_seconds) > end
    ):
        raise ValueError("approved run window cannot fit the full pilot")

    daemon_restart = _run_runtime_command(
        manifest,
        "daemon",
        "restart",
        timeout=30,
    )
    daemon_status = _run_runtime_command(
        manifest,
        "daemon",
        "status",
        "--json",
        timeout=30,
    )
    status_payload = _json_output(daemon_status, code="SAC_DAEMON_UNAVAILABLE")
    expected_config = str(
        _resolve_file(
            manifest.provider_config_ref, Path(manifest.workspace_path)
        ).resolve()
    )
    if (
        daemon_restart.returncode != 0
        or not status_payload.get("reachable")
        or str(status_payload.get("config_path") or "") != expected_config
        or str(status_payload.get("remote_config_path") or "") != expected_config
    ):
        raise ValueError("SAC_DAEMON_UNAVAILABLE")

    enforced = manifest.slo["enforced_limits"]
    start_result = _run_runtime_command(
        manifest,
        "autonomy",
        "start",
        "--unattended",
        "--json",
        "--goal-file",
        str(_resolve_file(manifest.goal_file, Path(manifest.workspace_path))),
        "--workspace",
        manifest.workspace_path,
        "--session",
        manifest.session_id,
        "--agent",
        manifest.agent_id,
        "--max-iterations",
        str(enforced["max_iterations"]),
        "--permission-profile",
        manifest.permission_profile,
        "--verification-domain",
        manifest.verification_domain,
        "--verify-command",
        str(manifest.slo["verifier_command"]),
        "--turn-timeout-seconds",
        str(enforced["turn_timeout_seconds"]),
        "--verification-timeout-seconds",
        str(enforced["verification_timeout_seconds"]),
        "--cycle-interval-seconds",
        str(manifest.cycle_interval_seconds),
        timeout=30,
    )
    start_payload = _json_output(start_result, code="SAC_COMMAND_REJECTED")
    autonomy_run = start_payload.get("run")
    if not isinstance(autonomy_run, dict):
        raise ValueError("SAC_COMMAND_REJECTED")
    durable_run_id = str(autonomy_run.get("run_id") or "").strip()
    if not durable_run_id:
        raise ValueError("SAC_COMMAND_REJECTED")

    started = datetime.now(UTC)
    metrics: dict[str, int | float] = {}
    recovery_events: list[dict[str, str]] = []
    restarted = False
    restart_checkpoint = ""
    restart_deadline: datetime | None = None
    target_end = started + timedelta(seconds=manifest.minimum_elapsed_seconds)
    while datetime.now(UTC) < target_end:
        if datetime.now(UTC) >= end:
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_WINDOW_TIMEOUT",
            )
        shown = _run_runtime_command(
            manifest,
            "autonomy",
            "show",
            durable_run_id,
            "--json",
            timeout=30,
        )
        if shown.returncode != 0:
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_EVIDENCE_MISSING",
            )
        show_payload = _json_output(shown, code="SAC_EVIDENCE_MISSING")
        shown_run = show_payload.get("run")
        if not isinstance(shown_run, dict):
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_EVIDENCE_MISSING",
            )
        if shown_run.get("status") in {"completed", "failed", "blocked", "cancelled"}:
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_EARLY_COMPLETION",
            )
        try:
            metrics, checkpoint_id, _gateway_run_id = _checkpoint_metrics(
                manifest,
                autonomy_run=shown_run,
            )
        except ValueError as exc:
            if str(exc) == "SAC_EVIDENCE_MISSING":
                if (
                    restart_deadline is not None
                    and datetime.now(UTC) >= restart_deadline
                ):
                    return _fail_live_run(
                        manifest,
                        run_id=durable_run_id,
                        manifest_path=manifest_path,
                        root=root,
                        started=started,
                        metrics=metrics,
                        recovery_events=recovery_events,
                        error_code="SAC_EVIDENCE_MISSING",
                    )
                try:
                    time.sleep(manifest.monitor_interval_seconds)
                except KeyboardInterrupt:
                    return _fail_live_run(
                        manifest,
                        run_id=durable_run_id,
                        manifest_path=manifest_path,
                        root=root,
                        started=started,
                        metrics=metrics,
                        recovery_events=recovery_events,
                        error_code="SAC_WINDOW_TIMEOUT",
                    )
                continue
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code=str(exc),
            )
        if _observed_limit_reached(metrics, manifest.slo["observed_limits"]):
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_OBSERVED_LIMIT_REACHED",
            )
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if not restarted and elapsed >= manifest.daemon_restart_offset_seconds:
            restart_checkpoint = checkpoint_id
            restart = _run_runtime_command(
                manifest,
                "daemon",
                "restart",
                timeout=30,
            )
            if restart.returncode != 0:
                return _fail_live_run(
                    manifest,
                    run_id=durable_run_id,
                    manifest_path=manifest_path,
                    root=root,
                    started=started,
                    metrics=metrics,
                    recovery_events=recovery_events,
                    error_code="SAC_DAEMON_UNAVAILABLE",
                )
            restarted = True
            restart_deadline = datetime.now(UTC) + timedelta(
                seconds=manifest.recovery_window_seconds
            )
            recovery_events.append(
                {
                    "kind": "restart",
                    "run_id": durable_run_id,
                    "occurred_at_utc": _utc_text(datetime.now(UTC)),
                }
            )
        elif (
            restarted
            and checkpoint_id != restart_checkpoint
            and len(recovery_events) == 1
        ):
            occurred = _utc_text(datetime.now(UTC))
            recovery_events.extend(
                {
                    "kind": kind,
                    "run_id": durable_run_id,
                    "occurred_at_utc": occurred,
                }
                for kind in ("reconnect", "interruption", "scheduled_wake")
            )
            restart_deadline = None
        elif restart_deadline is not None and datetime.now(UTC) >= restart_deadline:
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_EVIDENCE_MISSING",
            )
        try:
            time.sleep(manifest.monitor_interval_seconds)
        except KeyboardInterrupt:
            return _fail_live_run(
                manifest,
                run_id=durable_run_id,
                manifest_path=manifest_path,
                root=root,
                started=started,
                metrics=metrics,
                recovery_events=recovery_events,
                error_code="SAC_WINDOW_TIMEOUT",
            )

    try:
        verifier = subprocess.run(
            shlex.split(str(manifest.slo["verifier_command"])),
            cwd=manifest.workspace_path,
            env=_runtime_environment(manifest),
            check=False,
            capture_output=True,
            text=True,
            timeout=int(enforced["verification_timeout_seconds"]),
        )
    except subprocess.TimeoutExpired:
        return _fail_live_run(
            manifest,
            run_id=durable_run_id,
            manifest_path=manifest_path,
            root=root,
            started=started,
            metrics=metrics,
            recovery_events=recovery_events,
            error_code="SAC_VERIFIER_TIMEOUT",
        )
    except KeyboardInterrupt:
        return _fail_live_run(
            manifest,
            run_id=durable_run_id,
            manifest_path=manifest_path,
            root=root,
            started=started,
            metrics=metrics,
            recovery_events=recovery_events,
            error_code="SAC_WINDOW_TIMEOUT",
        )
    cancel_error = _cancel_run(manifest, durable_run_id)
    passed = (
        verifier.returncode == 0
        and cancel_error is None
        and bool(metrics)
        and FULL_RECOVERY_CHECKS.issubset({item["kind"] for item in recovery_events})
    )
    return _write_live_result(
        manifest,
        manifest_path=manifest_path,
        root=root,
        outcome="pass" if passed else "failed_certification",
        started=started,
        metrics=metrics,
        recovery_events=recovery_events,
        error_code=(
            None
            if passed
            else cancel_error
            or (
                "SAC_VERIFIER_FAILED" if verifier.returncode else "SAC_EVIDENCE_MISSING"
            )
        ),
        verifier_passed=verifier.returncode == 0,
    )


def _render_report_markdown(payload: dict[str, Any]) -> str:
    manifest = payload["manifest"]
    evidence = payload.get("evidence") or {}
    metrics = evidence.get("metrics") or {}
    lines = [
        f"# Sustained Autonomy Certification ({payload['run_id']})",
        "",
        f"- Schema: `{payload['schema_version']}`",
        f"- Outcome: `{payload['outcome']}`",
        f"- Certification level: `{payload['certification_level']}`",
        f"- Validation only: `{payload['validation_only']}`",
        f"- Pilot kind: `{manifest['pilot_kind']}`",
        f"- Minimum elapsed seconds: `{manifest['minimum_elapsed_seconds']}`",
        f"- Provider config: `{manifest['provider_config_ref']}`",
        f"- Redaction: `{payload['redaction_status']}`",
    ]
    if evidence:
        lines.extend(
            [
                f"- Started: `{evidence.get('started_at_utc', '-')}`",
                f"- Ended: `{evidence.get('ended_at_utc', '-')}`",
                f"- Elapsed seconds: `{metrics.get('elapsed_seconds', 0)}`",
                f"- Active seconds: `{metrics.get('active_seconds', 0)}`",
                f"- Idle seconds: `{metrics.get('idle_seconds', 0)}`",
                f"- Iterations: `{metrics.get('iteration', 0)}`",
                f"- Tokens: `{metrics.get('token', 0)}`",
                f"- Estimated cost USD: `{metrics.get('cost', 0)}`",
                f"- Verifier passed: `{evidence.get('verifier_passed', False)}`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    return payload


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required field: {key}")
    return value.strip()


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"missing required integer field: {key}")
    return value


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def _require_mapping_keys(
    payload: dict[str, Any],
    key: str,
    required_keys: tuple[str, ...],
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"missing required mapping: {key}")
    missing = [item for item in required_keys if item not in value]
    if missing:
        raise ValueError(f"{key} is missing required keys: {', '.join(missing)}")
    return value


def _has_secret_bearing_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(
                token in lowered
                for token in (
                    "secret",
                    "api_key",
                    "password",
                    "credential",
                    "access_token",
                    "refresh_token",
                    "bearer_token",
                )
            ):
                return True
            if _has_secret_bearing_field(child):
                return True
    if isinstance(value, list):
        return any(_has_secret_bearing_field(item) for item in value)
    return False


def _safe_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "FULL_RECOVERY_CHECKS",
    "MINIMUM_ELAPSED_SECONDS",
    "INTERIM_PILOT_KINDS",
    "REPORT_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "CertificationManifest",
    "validate_certification_manifest",
    "run_certification",
    "write_certification_report",
]
