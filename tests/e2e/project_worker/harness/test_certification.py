from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.e2e.project_worker.harness.certification import (
    REPORT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    validate_certification_manifest,
    write_certification_report,
)

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[4]


def _manifest(
    tmp_path: Path,
    *,
    pilot_kind: str = "research-8h",
    minimum_elapsed_seconds: int = 28_800,
) -> dict[str, object]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    now = datetime(2026, 8, 7, tzinfo=UTC)
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": f"{pilot_kind}-fixture",
        "pilot_kind": pilot_kind,
        "approved_start_utc": now.isoformat().replace("+00:00", "Z"),
        "approved_end_utc": (now + timedelta(seconds=minimum_elapsed_seconds))
        .isoformat()
        .replace("+00:00", "Z"),
        "approval_expires_utc": "2099-01-01T00:00:00Z",
        "minimum_elapsed_seconds": minimum_elapsed_seconds,
        "workspace_path": str(workspace),
        "approved_workspace_path": str(workspace),
        "source_revision": "fixture",
        "provider": {
            "config_ref": "test-configs/redacted-provider.json",
            "agent_id": "fixture-agent",
            "model": "fixture-model",
            "profile": "fixture-profile",
        },
        "goal_file": "goal.md",
        "slo": {
            "verifier_command": "pytest -q",
            "verifier_criteria": "exit 0",
            "budgets": {
                "token": 1,
                "cost": 1,
                "retry": 1,
                "iteration": 1,
                "storage": 1,
                "wall_clock": minimum_elapsed_seconds,
            },
            "recovery_checks": ["restart", "wake"],
            "side_effect_scope": "none",
        },
    }


def _write_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("pilot_kind", "minimum"),
    (
        ("research-2h-interim", 7_200),
        ("research-8h", 28_800),
        ("code-24h", 86_400),
    ),
)
def test_certification_manifest_accepts_valid_pilot_kinds(
    tmp_path: Path,
    pilot_kind: str,
    minimum: int,
) -> None:
    path = _write_manifest(
        tmp_path,
        _manifest(tmp_path, pilot_kind=pilot_kind, minimum_elapsed_seconds=minimum),
    )

    manifest = validate_certification_manifest(
        path,
        now=datetime(2026, 8, 7, tzinfo=UTC),
    )

    assert manifest.pilot_kind == pilot_kind
    assert manifest.minimum_elapsed_seconds == minimum


@pytest.mark.parametrize(
    "mutation",
    ("compressed", "undersized", "missing_slo", "expired", "unapproved_workspace"),
)
def test_certification_manifest_rejects_invalid_cases(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = _manifest(tmp_path)
    if mutation == "compressed":
        payload["compressed_fixture"] = True
    elif mutation == "undersized":
        payload["minimum_elapsed_seconds"] = 120
    elif mutation == "missing_slo":
        payload.pop("slo")
    elif mutation == "expired":
        payload["approval_expires_utc"] = "2020-01-01T00:00:00Z"
    elif mutation == "unapproved_workspace":
        payload["approved_workspace_path"] = str(tmp_path / "other")
    path = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError):
        validate_certification_manifest(path, now=datetime(2026, 8, 7, tzinfo=UTC))


def test_certification_report_writes_generated_root_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    path = _write_manifest(tmp_path, _manifest(tmp_path))
    manifest = validate_certification_manifest(
        path,
        now=datetime(2026, 8, 7, tzinfo=UTC),
    )

    json_path, markdown_path = write_certification_report(
        manifest,
        manifest_path=path,
        root=tmp_path,
        validation_only=True,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["outcome"] == "blocked_external"
    assert payload["certification_level"] == "full_certification"
    assert "test-configs/redacted-provider.json" in markdown_path.read_text()
    assert "api_key" not in json_path.read_text(encoding="utf-8").lower()


def test_interim_two_hour_report_is_not_full_certification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    path = _write_manifest(
        tmp_path,
        _manifest(
            tmp_path,
            pilot_kind="research-2h-interim",
            minimum_elapsed_seconds=7_200,
        ),
    )
    manifest = validate_certification_manifest(
        path,
        now=datetime(2026, 8, 7, tzinfo=UTC),
    )

    json_path, markdown_path = write_certification_report(
        manifest,
        manifest_path=path,
        root=tmp_path,
        validation_only=True,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["certification_level"] == "interim_support"
    assert "Certification level: `interim_support`" in markdown_path.read_text()


def test_certify_runner_validate_only_writes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    manifest_path = _write_manifest(tmp_path, _manifest(tmp_path))

    result = subprocess.run(
        [
            sys.executable,
            "tests/e2e/runners/run_project_worker_e2e.py",
            "certify",
            "--manifest",
            str(manifest_path),
            "--validate-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "research-8h-fixture" in result.stdout
    assert "certification-report.json" in result.stdout


def test_project_worker_list_keeps_existing_modes() -> None:
    result = subprocess.run(
        [sys.executable, "tests/e2e/runners/run_project_worker_e2e.py", "list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "pilot-artifacts" in result.stdout
    assert "soak-artifacts" in result.stdout
