from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shlex
import subprocess
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
    source_revision: str
    provider_config_ref: str
    agent_id: str
    model: str
    profile: str
    goal_file: str
    execution_command: str
    evidence_file: str
    slo: dict[str, Any]


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
    source_revision = _required_str(payload, "source_revision")
    if for_live_run:
        _require_source_revision(workspace, source_revision)

    slo = _require_mapping_keys(
        payload,
        "slo",
        (
            "verifier_command",
            "verifier_criteria",
            "budgets",
            "recovery_checks",
            "side_effect_scope",
        ),
    )
    execution_command = str(slo.get("execution_command") or "").strip()
    evidence_file = str(slo.get("evidence_file") or "").strip()
    if for_live_run and (not execution_command or not evidence_file):
        raise ValueError(
            "live certification requires execution_command and evidence_file"
        )
    budgets = _require_mapping_keys(
        slo,
        "budgets",
        ("token", "cost", "retry", "iteration", "storage", "wall_clock"),
    )
    if any(
        not isinstance(value, (int, float)) or value < 0 for value in budgets.values()
    ):
        raise ValueError("slo budgets must be non-negative numbers")
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
    if _has_secret_bearing_field(payload):
        raise ValueError("manifest contains a secret-bearing field")

    return CertificationManifest(
        run_id=_required_str(payload, "run_id"),
        pilot_kind=cast(PilotKind, pilot_kind),
        approved_start_utc=_utc_text(start),
        approved_end_utc=_utc_text(end),
        minimum_elapsed_seconds=minimum,
        workspace_path=str(workspace.resolve(strict=False)),
        source_revision=source_revision,
        provider_config_ref=str(provider["config_ref"]),
        agent_id=str(provider["agent_id"]),
        model=str(provider["model"]),
        profile=str(provider["profile"]),
        goal_file=_required_str(payload, "goal_file"),
        execution_command=execution_command,
        evidence_file=evidence_file,
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
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_report_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def run_certification(
    manifest: CertificationManifest,
    *,
    manifest_path: Path,
    root: Path,
) -> tuple[int, Path, Path]:
    if not manifest.execution_command or not manifest.evidence_file:
        raise ValueError(
            "live certification requires execution_command and evidence_file"
        )
    _require_source_revision(Path(manifest.workspace_path), manifest.source_revision)
    current = datetime.now(UTC)
    start = _parse_utc(manifest.approved_start_utc)
    end = _parse_utc(manifest.approved_end_utc)
    if (
        current < start
        or current + timedelta(seconds=manifest.minimum_elapsed_seconds) > end
    ):
        raise ValueError("approved run window cannot fit the full pilot")

    started = datetime.now(UTC)
    execution = subprocess.run(
        shlex.split(manifest.execution_command),
        cwd=manifest.workspace_path,
        check=False,
        timeout=max(1, int((end - current).total_seconds())),
    )
    ended = datetime.now(UTC)
    elapsed = (ended - started).total_seconds()
    evidence_path = Path(manifest.evidence_file).expanduser()
    if not evidence_path.is_absolute():
        evidence_path = Path(manifest.workspace_path) / evidence_path
    evidence = _read_json(evidence_path) if evidence_path.exists() else {}
    evidence_run_matches = evidence.get("run_id") == manifest.run_id
    metrics = dict(evidence.get("metrics") or {}) if evidence_run_matches else {}
    metrics.update({"elapsed_seconds": elapsed, "wall_clock": elapsed})
    for key in ("token", "cost", "retry", "iteration", "storage"):
        metrics.setdefault(key, 0)
    budgets = manifest.slo["budgets"]
    budget_checks = {
        key: metrics[key] <= float(limit) for key, limit in budgets.items()
    }

    verifier = subprocess.run(
        shlex.split(str(manifest.slo["verifier_command"])),
        cwd=manifest.workspace_path,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=1_800,
    )
    recovery_events = []
    if evidence_run_matches:
        for item in evidence.get("recovery_events") or []:
            if not isinstance(item, dict) or item.get("run_id") != manifest.run_id:
                continue
            occurred_at = item.get("occurred_at_utc")
            if not isinstance(occurred_at, str):
                continue
            try:
                occurred = _parse_utc(occurred_at)
            except ValueError:
                continue
            if start <= occurred <= end:
                recovery_events.append(item)
    observed_recovery = {str(item.get("kind") or "") for item in recovery_events}
    outcome = (
        "pass"
        if execution.returncode == 0
        and evidence_run_matches
        and elapsed >= manifest.minimum_elapsed_seconds
        and verifier.returncode == 0
        and set(manifest.slo["recovery_checks"]).issubset(observed_recovery)
        and all(budget_checks.values())
        else "failed_certification"
    )
    result = {
        "outcome": outcome,
        "started_at_utc": _utc_text(started),
        "ended_at_utc": _utc_text(ended),
        "execution_exit_code": execution.returncode,
        "verifier_passed": verifier.returncode == 0,
        "verifier_summary": next(
            (line.strip() for line in verifier.stdout.splitlines() if line.strip()),
            "verifier produced no output",
        ),
        "metrics": metrics,
        "budget_checks": budget_checks,
        "recovery_events": recovery_events,
        "memory_help": "not_applicable:no memory-help claim made",
        "evidence_file": str(evidence_path),
    }
    paths = write_certification_report(
        manifest,
        manifest_path=manifest_path,
        root=root,
        validation_only=False,
        evidence=result,
    )
    return (0 if outcome == "pass" else 1), *paths


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
