from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.e2e.project_worker.harness import certification
from tests.e2e.project_worker.harness.certification import (
    REPORT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    validate_certification_manifest,
    run_certification,
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
    (workspace / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "fixture.txt"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=OpenMinion Tests",
            "-c",
            "user.email=tests@openminion.local",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=workspace,
        check=True,
    )
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
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
        "source_revision": source_revision,
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
            "recovery_checks": [
                "restart",
                "reconnect",
                "interruption",
                "scheduled_wake",
            ],
            "side_effect_scope": "none",
            "execution_command": "python -c pass",
            "evidence_file": "evidence.json",
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
        for_live_run=True,
    )

    assert manifest.pilot_kind == pilot_kind
    assert manifest.minimum_elapsed_seconds == minimum


def test_certification_manifest_freezes_live_paths(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, _manifest(tmp_path))

    manifest = validate_certification_manifest(
        path,
        now=datetime(2026, 8, 7, tzinfo=UTC),
    )
    manifest.slo["execution_command"] = "changed after validation"
    manifest.slo["evidence_file"] = "changed.json"

    assert manifest.execution_command == "python -c pass"
    assert manifest.evidence_file == "evidence.json"


@pytest.mark.parametrize(
    "mutation",
    (
        "compressed",
        "undersized",
        "missing_slo",
        "expired",
        "unapproved_workspace",
        "revision_mismatch",
        "missing_execution_command",
        "missing_evidence_file",
    ),
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
    elif mutation == "revision_mismatch":
        payload["source_revision"] = "not-the-approved-revision"
    elif mutation == "missing_execution_command":
        payload["slo"].pop("execution_command")
    elif mutation == "missing_evidence_file":
        payload["slo"].pop("evidence_file")
    path = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError):
        validate_certification_manifest(
            path,
            now=datetime(2026, 8, 7, tzinfo=UTC),
            for_live_run=mutation
            in {
                "revision_mismatch",
                "missing_execution_command",
                "missing_evidence_file",
            },
        )


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


def test_certify_runner_validate_only_accepts_legacy_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    payload = _manifest(tmp_path)
    payload["source_revision"] = "legacy-validation-only"
    payload["slo"].pop("execution_command")
    payload["slo"].pop("evidence_file")
    manifest_path = _write_manifest(tmp_path, payload)

    result = subprocess.run(
        [
            sys.executable,
            "tests/e2e/runners/run_project_worker_e2e.py",
            "certify",
            "--manifest",
            str(manifest_path),
            "--validate-only",
        ],
        cwd=Path(__file__).resolve().parents[4],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
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


def test_live_certification_rejects_compressed_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("OPENMINION_GENERATED_ROOT", raising=False)
    payload = _manifest(tmp_path)
    now = datetime.now(UTC)
    payload["approved_start_utc"] = (now - timedelta(minutes=1)).isoformat()
    payload["approved_end_utc"] = (now + timedelta(hours=9)).isoformat()
    payload["slo"]["budgets"] = {
        "token": 100_000,
        "cost": 10,
        "retry": 10,
        "iteration": 10,
        "storage": 10_000_000,
        "wall_clock": 40_000,
    }
    workspace = Path(str(payload["workspace_path"]))
    evidence_path = workspace / "evidence.json"
    recovery_kinds = (
        "restart",
        "reconnect",
        "interruption",
        "scheduled_wake",
    )
    evidence = {
        "run_id": payload["run_id"],
        "metrics": {
            "token": 1,
            "cost": 0.01,
            "retry": 0,
            "iteration": 1,
            "storage": 1,
        },
        "recovery_events": [
            {
                "kind": kind,
                "run_id": payload["run_id"],
                "occurred_at_utc": now.isoformat(),
            }
            for kind in recovery_kinds
        ]
        + [
            {
                "kind": "restart",
                "run_id": "another-run",
                "occurred_at_utc": now.isoformat(),
            },
            {
                "kind": "restart",
                "run_id": payload["run_id"],
                "occurred_at_utc": (now + timedelta(hours=10)).isoformat(),
            },
        ],
    }
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    payload["slo"]["evidence_file"] = str(evidence_path)
    goal_path = tmp_path / "goal.md"
    goal_path.write_text("complete the bounded fixture", encoding="utf-8")
    payload["goal_file"] = str(goal_path)
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = validate_certification_manifest(
        manifest_path,
        now=now,
    )

    def successful_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        stdout = manifest.source_revision if args[0] == "git" else "verified\n"
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(certification.subprocess, "run", successful_run)
    exit_code, json_path, _ = run_certification(
        manifest,
        manifest_path=manifest_path,
        root=tmp_path,
    )
    report = json.loads(json_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["outcome"] == "failed_certification"
    assert report["evidence"]["verifier_passed"] is True
    assert report["evidence"]["metrics"]["elapsed_seconds"] < 28_800
    assert {item["kind"] for item in report["evidence"]["recovery_events"]} == set(
        recovery_kinds
    )
    assert all(
        item["run_id"] == manifest.run_id
        for item in report["evidence"]["recovery_events"]
    )

    evidence["run_id"] = "another-run"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    exit_code, json_path, _ = run_certification(
        replace(manifest, minimum_elapsed_seconds=0),
        manifest_path=manifest_path,
        root=tmp_path,
    )
    report = json.loads(json_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["evidence"]["recovery_events"] == []
    assert report["evidence"]["metrics"]["token"] == 0


def test_live_certification_rechecks_source_revision(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path)
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = validate_certification_manifest(
        manifest_path,
        now=datetime(2026, 8, 7, tzinfo=UTC),
    )
    workspace = Path(manifest.workspace_path)
    (workspace / "changed.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "changed.txt"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=OpenMinion Tests",
            "-c",
            "user.email=tests@openminion.local",
            "commit",
            "-qm",
            "change revision",
        ],
        cwd=workspace,
        check=True,
    )

    with pytest.raises(ValueError, match="revision"):
        run_certification(manifest, manifest_path=manifest_path, root=tmp_path)
