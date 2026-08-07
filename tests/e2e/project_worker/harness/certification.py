from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal, cast

from openminion.base.generated_paths import resolve_generated_root

RUN_SCHEMA_VERSION = "sustained-autonomy-certification-run.v1"
REPORT_SCHEMA_VERSION = "sustained-autonomy-certification-report.v1"
REPORT_DIRNAME = "sustained-autonomy-certification"
MINIMUM_ELAPSED_SECONDS = {
    "research-8h": 28_800,
    "code-24h": 86_400,
}

PilotKind = Literal["research-8h", "code-24h"]


@dataclass(frozen=True)
class CertificationManifest:
    run_id: str
    pilot_kind: PilotKind
    approved_start_utc: str
    approved_end_utc: str
    minimum_elapsed_seconds: int
    workspace_path: str
    provider_config_ref: str
    agent_id: str
    model: str
    profile: str
    goal_file: str


def validate_certification_manifest(
    path: Path,
    *,
    now: datetime | None = None,
) -> CertificationManifest:
    payload = _read_json(path)
    if payload.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ValueError("unsupported sustained autonomy manifest schema_version")
    if payload.get("compressed_fixture") or payload.get("compressed"):
        raise ValueError("compressed fixtures cannot be certification manifests")

    pilot_kind = _required_str(payload, "pilot_kind")
    if pilot_kind not in MINIMUM_ELAPSED_SECONDS:
        raise ValueError("pilot_kind must be research-8h or code-24h")

    minimum = _required_int(payload, "minimum_elapsed_seconds")
    required_minimum = MINIMUM_ELAPSED_SECONDS[pilot_kind]
    if minimum < required_minimum:
        raise ValueError("minimum_elapsed_seconds is below the pilot minimum")

    start = _parse_utc(_required_str(payload, "approved_start_utc"))
    end = _parse_utc(_required_str(payload, "approved_end_utc"))
    if (end - start).total_seconds() < minimum:
        raise ValueError("approved window is shorter than minimum elapsed seconds")

    approval_expires = _parse_utc(_required_str(payload, "approval_expires_utc"))
    if approval_expires <= (now or datetime.now(UTC)):
        raise ValueError("manifest approval has expired")

    workspace = Path(_required_str(payload, "workspace_path")).expanduser()
    approved_workspace = Path(
        _required_str(payload, "approved_workspace_path")
    ).expanduser()
    if workspace.resolve(strict=False) != approved_workspace.resolve(strict=False):
        raise ValueError("workspace_path does not match approved_workspace_path")
    if not workspace.exists():
        raise ValueError("approved workspace does not exist")

    _require_mapping_keys(
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
        approved_start_utc=start.isoformat().replace("+00:00", "Z"),
        approved_end_utc=end.isoformat().replace("+00:00", "Z"),
        minimum_elapsed_seconds=minimum,
        workspace_path=str(workspace.resolve(strict=False)),
        provider_config_ref=str(provider["config_ref"]),
        agent_id=str(provider["agent_id"]),
        model=str(provider["model"]),
        profile=str(provider["profile"]),
        goal_file=_required_str(payload, "goal_file"),
    )


def write_certification_report(
    manifest: CertificationManifest,
    *,
    manifest_path: Path,
    root: Path,
    validation_only: bool,
) -> tuple[Path, Path]:
    output_root = (
        resolve_generated_root(root) / REPORT_DIRNAME / _safe_segment(manifest.run_id)
    )
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "validation_only": validation_only,
        "outcome": "blocked_external" if validation_only else "not_started",
        "manifest_path": str(manifest_path.resolve(strict=False)),
        "manifest": asdict(manifest),
        "redaction_status": "redacted",
        "raw_provider_response_ref": None,
    }
    json_path = output_root / "certification-report.json"
    markdown_path = output_root / "certification-report.md"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_report_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def _render_report_markdown(payload: dict[str, Any]) -> str:
    manifest = payload["manifest"]
    lines = [
        f"# Sustained Autonomy Certification ({payload['run_id']})",
        "",
        f"- Schema: `{payload['schema_version']}`",
        f"- Outcome: `{payload['outcome']}`",
        f"- Validation only: `{payload['validation_only']}`",
        f"- Pilot kind: `{manifest['pilot_kind']}`",
        f"- Minimum elapsed seconds: `{manifest['minimum_elapsed_seconds']}`",
        f"- Provider config: `{manifest['provider_config_ref']}`",
        f"- Redaction: `{payload['redaction_status']}`",
        "",
    ]
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


__all__ = [
    "MINIMUM_ELAPSED_SECONDS",
    "REPORT_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "CertificationManifest",
    "validate_certification_manifest",
    "write_certification_report",
]
