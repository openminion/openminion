from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit_all(workspace: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=OpenMinion Tests",
            "-c",
            "user.email=tests@openminion.local",
            "commit",
            "-qm",
            message,
        ],
        cwd=workspace,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _live_manifest(tmp_path: Path) -> dict[str, object]:
    payload = _manifest(tmp_path)
    workspace = Path(str(payload["workspace_path"]))
    provider_config = workspace / "provider.json"
    provider_config.write_text("{}\n", encoding="utf-8")
    goal = workspace / "goal.md"
    goal.write_text("complete the approved project\n", encoding="utf-8")
    payload.pop("source_revision")
    payload["workspace_revision"] = _commit_all(workspace, "live inputs")

    runtime_root = tmp_path / "runtime"
    package = runtime_root / "src" / "openminion"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=runtime_root, check=True)
    runtime_revision = _commit_all(runtime_root, "runtime")
    for name in ("home", "data", "generated"):
        (tmp_path / name).mkdir()

    verifier = f'{sys.executable} -c "raise SystemExit(0)"'
    artifact_contract = "one verifier-backed project artifact"
    payload.update(
        {
            "runtime_python": sys.executable,
            "runtime_source_root": str(runtime_root),
            "runtime_source_revision": runtime_revision,
            "home_root": str(tmp_path / "home"),
            "data_root": str(tmp_path / "data"),
            "generated_root": str(tmp_path / "generated"),
            "session_id": "sac-session",
            "permission_profile": "local-safe",
            "verification_domain": "research",
            "goal_file": str(goal),
            "goal_sha256": _sha256_file(goal),
            "cycle_interval_seconds": 5,
            "monitor_interval_seconds": 1,
            "recovery_window_seconds": 8,
            "daemon_restart_offset_seconds": 0,
            "provider": {
                "config_ref": str(provider_config),
                "config_sha256": _sha256_file(provider_config),
                "agent_id": "fixture-agent",
                "model": "fixture-model",
                "profile": "fixture-profile",
            },
            "slo": {
                "verifier_command": verifier,
                "verifier_command_sha256": _sha256_text(verifier),
                "verifier_criteria": "exit 0",
                "expected_artifact_contract": artifact_contract,
                "expected_artifact_contract_sha256": _sha256_text(artifact_contract),
                "enforced_limits": {
                    "max_iterations": 100,
                    "turn_timeout_seconds": 1,
                    "verification_timeout_seconds": 1,
                },
                "observed_limits": {
                    "token": 100_000,
                    "cost": 100,
                    "retry": 10,
                    "tool_call": 100,
                    "storage": 100_000_000,
                    "cost_provenance": "provider_or_estimated",
                    "side_effect_measurement": "not_supported",
                    "side_effect_limit": None,
                },
                "operator_limits": {
                    "provider_spend_cap": "operator-console-cap",
                    "isolated_workspace": True,
                    "remote_write_allowed": False,
                },
                "recovery_checks": sorted(certification.FULL_RECOVERY_CHECKS),
                "side_effect_scope": "isolated-workspace",
                "planned_approval_points": ["start", "recovery-restart"],
            },
        }
    )
    return payload


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
    path = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError):
        validate_certification_manifest(
            path,
            now=datetime(2026, 8, 7, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "caller_command",
        "config_hash",
        "goal_hash",
        "monitor_interval",
        "recovery_window",
        "side_effect_claim",
    ),
)
def test_live_manifest_rejects_untrusted_or_unbounded_inputs(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = _live_manifest(tmp_path)
    if mutation == "caller_command":
        payload["slo"]["execution_command"] = "python arbitrary.py"
    elif mutation == "config_hash":
        payload["provider"]["config_sha256"] = "0" * 64
    elif mutation == "goal_hash":
        payload["goal_sha256"] = "0" * 64
    elif mutation == "monitor_interval":
        payload["monitor_interval_seconds"] = payload["cycle_interval_seconds"]
    elif mutation == "recovery_window":
        payload["recovery_window_seconds"] = 1
    elif mutation == "side_effect_claim":
        payload["slo"]["observed_limits"]["side_effect_limit"] = 0

    with pytest.raises(ValueError):
        validate_certification_manifest(
            _write_manifest(tmp_path, payload),
            now=datetime(2026, 8, 7, tzinfo=UTC),
            for_live_run=True,
        )


def test_live_manifest_binds_canonical_runtime_inputs(tmp_path: Path) -> None:
    payload = _live_manifest(tmp_path)

    manifest = validate_certification_manifest(
        _write_manifest(tmp_path, payload),
        now=datetime(2026, 8, 7, tzinfo=UTC),
        for_live_run=True,
    )

    assert manifest.execution_command == ""
    assert manifest.evidence_file == ""
    assert manifest.cycle_interval_seconds == 5


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


def test_live_certification_builds_only_canonical_project_worker_command(
    tmp_path: Path,
) -> None:
    payload = _live_manifest(tmp_path)
    manifest = validate_certification_manifest(
        _write_manifest(tmp_path, payload),
        now=datetime(2026, 8, 7, tzinfo=UTC),
        for_live_run=True,
    )

    prefix = certification._runtime_command_prefix(manifest)

    assert prefix[:3] == [sys.executable, "-m", "openminion"]
    assert prefix[-2:] == ["--generated-root", manifest.generated_root]
    assert "execution_command" not in prefix
    assert "evidence_file" not in prefix


def test_live_certification_rechecks_source_revision(
    tmp_path: Path,
) -> None:
    payload = _live_manifest(tmp_path)
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = validate_certification_manifest(
        manifest_path,
        now=datetime(2026, 8, 7, tzinfo=UTC),
        for_live_run=True,
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


@pytest.mark.parametrize(
    ("error", "expected_returncode"),
    (
        (subprocess.TimeoutExpired(cmd=("openminion",), timeout=30), 124),
        (KeyboardInterrupt(), 130),
    ),
)
def test_runtime_command_interrupt_is_a_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected_returncode: int,
) -> None:
    payload = _live_manifest(tmp_path)
    manifest = validate_certification_manifest(
        _write_manifest(tmp_path, payload),
        now=datetime(2026, 8, 7, tzinfo=UTC),
        for_live_run=True,
    )

    def timeout(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(certification.subprocess, "run", timeout)

    result = certification._run_runtime_command(
        manifest,
        "daemon",
        "status",
        "--json",
        timeout=30,
    )

    assert result.returncode == expected_returncode


def test_live_certification_rejects_daemon_config_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _live_manifest(tmp_path)
    now = datetime.now(UTC)
    payload["approved_start_utc"] = (now - timedelta(minutes=1)).isoformat()
    payload["approved_end_utc"] = (now + timedelta(hours=10)).isoformat()
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = validate_certification_manifest(
        manifest_path,
        now=now,
        for_live_run=True,
    )
    calls: list[tuple[str, ...]] = []

    def runtime_command(
        _manifest: certification.CertificationManifest,
        *arguments: str,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(arguments)
        payload = (
            json.dumps(
                {
                    "reachable": True,
                    "config_path": str(tmp_path / "wrong.json"),
                    "remote_config_path": str(tmp_path / "wrong.json"),
                }
            )
            if arguments == ("daemon", "status", "--json")
            else ""
        )
        return subprocess.CompletedProcess(arguments, 0, stdout=payload, stderr="")

    monkeypatch.setattr(certification, "_run_runtime_command", runtime_command)

    with pytest.raises(ValueError, match="SAC_DAEMON_UNAVAILABLE"):
        run_certification(manifest, manifest_path=manifest_path, root=tmp_path)

    assert calls == [("daemon", "restart"), ("daemon", "status", "--json")]
