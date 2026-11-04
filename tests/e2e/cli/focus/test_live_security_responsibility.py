"""Opt-in MiniMax and Docker proof of one finite schedule across approved repair."""

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from unittest.mock import patch

import pytest

from openminion.cli.commands.autonomy_project import configured_cron_store
from openminion.cli.commands.daemon import (
    build_daemon_status_payload,
    daemon_start,
    daemon_stop,
)
from openminion.modules.artifact.config import (
    ArtifactCtlConfig,
    BlobStoreConfig,
    IndexConfig,
)
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.task import TaskManager
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.task.surface import build_task_surface
from openminion.modules.telemetry.usage import usage_event_session_id
from openminion.tools.security.review_schemas import SecurityFindingReview
from openminion.tools.security.schedule import SourceScanAdmission
from openminion.tools.security.schemas import SecurityAuditReport, SecurityScanResult
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root
from tests.e2e.cli.focus.harness.probe import focus_session_id
from tests.e2e.cli.focus.test_live_security_remediation import (
    _PARENT,
    _RESEARCHER,
    _REVIEWER,
    _configuration,
    _fixture,
    _git,
    _handoff,
    _install_roles,
    _operator_evidence,
    _published,
    _readonly_turn,
    _repair,
)
from tests.e2e.cli.focus.test_live_goal_only import _events

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(2400)]


def _schedule_configuration(root, target, monkeypatch):
    path, model, tokens = _configuration(root, target, monkeypatch)
    config = json.loads(path.read_text())
    scan_config = root / "scan-config.json"
    scan_config.write_text(
        json.dumps(
            {
                "docker": {
                    "daemon_socket": os.environ[
                        "OPENMINION_TEST_SECURITY_LAB_DOCKER_SOCKET"
                    ],
                    "worker_image": os.environ["OPENMINION_TEST_SECURITY_SCAN_IMAGE"],
                },
                "targets": {},
            }
        )
    )
    rules = target / "rule.yml"
    admission = SourceScanAdmission(
        schema_version="security-source-schedule/v1",
        agent_id=_RESEARCHER,
        root=target,
        checks=["code"],
        allowed_tools=["security.scan_code", "security.publish_report"],
        config_fingerprint=sha256(
            scan_config.read_bytes() + rules.read_bytes()
        ).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        max_runs=2,
        timeout_seconds=300,
    )
    admissions = root / "schedule-config.json"
    admissions.write_text(json.dumps({_RESEARCHER: admission.model_dump(mode="json")}))
    config["runtime"]["env"].update(
        OPENMINION_SECURITY_SCAN_CONFIG=str(scan_config),
        OPENMINION_SECURITY_SCHEDULE_CONFIG=str(admissions),
    )
    path.write_text(json.dumps(config))
    return path, model, tokens, admission


def _wait_scan(manager, job_id, prior_run_id=None):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        runs = manager.list_scheduled_runs(job_id=job_id)
        for run in runs:
            if run["run_id"] == prior_run_id or run["state"] == "cancelled":
                continue
            if run["state"] in {"finished", "failed", "timed_out"}:
                assert run["state"] == "finished", run
                manager.reconcile_scheduled_outcomes()
                output = run["output"]["security_scan"]
                assert output["status"] == "completed", output
                assert (
                    output["baseline_ref"] is None and output["comparison_ref"] is None
                )
                return run
        time.sleep(0.1)
    raise AssertionError("scheduled scan did not complete within 600 seconds")


def _read_report(ctl, run, revision, image, socket):
    result = run["output"]["security_scan"]
    report = SecurityAuditReport.model_validate_json(
        ctl.read_bytes(result["report_ref"])
    )
    assert report.execution_status == "completed"
    assert (
        report.scope.target_revision == revision
        and report.scope.requested_checks == ["code"]
    )
    metadata = ctl.get(result["report_ref"])
    assert metadata.agent_id == _RESEARCHER
    assert metadata.session_id == usage_event_session_id(
        {
            "session_id": f"cron:{run['job_id']}:{run['run_id']}",
            "metadata": {"conversation_id": run["run_id"]},
        }
    )
    assert ctl.read_bytes(result["sarif_ref"])
    for ref in report.evidence_refs:
        scan = SecurityScanResult.model_validate_json(ctl.read_bytes(ref))
        assert scan.status == "completed" and scan.target_revision == revision
        assert scan.provenance.worker_image_digest == image
        assert scan.provenance.cleanup_completed is True
        assert (
            scan.configuration_identity.config_sha256
            == sha256(
                Path(os.environ["OPENMINION_TEST_SECURITY_SCAN_RULES"]).read_bytes()
            ).hexdigest()
        )
        workers = subprocess.run(
            [
                "docker",
                "--host",
                socket,
                "container",
                "ls",
                "-aq",
                "--no-trunc",
                "--filter",
                f"label=openminion.security_scan.session={metadata.session_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert not workers.stdout.strip(), "scheduled scan left a Docker worker"
    return result["report_ref"], report


def _observed_phase_usage(events, roles):
    by_role = {
        role: [
            event
            for event in events
            if event["agent_id"] == role and event["event_type"] == "chat.phase_timing"
        ]
        for role in roles
    }
    phases = [event["data"] for records in by_role.values() for event in records]
    totals = {}
    fields = ("provider_input_tokens", "provider_output_tokens")
    for field in fields:
        values = [phase[field] for phase in phases if isinstance(phase.get(field), int)]
        totals[field.removeprefix("provider_")] = sum(values) if values else None
    return {
        "usage_scope": "Observed canonical phase aggregates; complete per-call accounting is unavailable.",
        "usage_by_role": by_role,
        "observed_tokens": totals,
        "phase_usage_fields_complete": all(by_role.values())
        and bool(phases)
        and all(
            isinstance(phase.get(field), int) for phase in phases for field in fields
        ),
        "per_call_accounting_complete": False,
    }


def _review(probe, root, ctl, stage, refs):
    previous = {item.sha256 for item in ctl.list_recent(limit=200)}
    if stage == "initial-review":
        instruction = (
            "Independently review this complete source evidence and inspect app.py/verify.py readonly. "
            "Call security.review_report review_kind=initial for the exact report ref. Disposition every "
            "candidate once, using JSON arrays for finding_reviews and verification_criteria. Give accepted "
            "findings a minimal objective and explicit regression criteria; keep the complete remediation brief "
            "under 1000 characters. "
        )
    else:
        instruction = (
            "Independently evaluate the original accepted finding against the fresh complete rescan. "
            "Call security.review_report review_kind=follow_up with the initial review ref and rescan report ref, "
            "one closure per accepted finding and decisions for unmatched current candidates. Use JSON arrays "
            "for closure_reviews; omit optional empty lists. For resolved outcomes omit next_step_guidance "
            "and current_finding_ids. Choose resolved/residual/not_reproduced using evidence. "
        )
    turn = _readonly_turn(
        probe,
        root,
        stage,
        instruction
        + "If publication rejects arguments, correct them using its feedback. After successful publication, "
        "return the canonical review ref and stop. Do not repair.\n"
        + _operator_evidence(probe, ctl, refs),
    )
    ref, document = _published(ctl, _REVIEWER, "security.review_report", previous)
    return ref, SecurityFindingReview.model_validate(document), turn


def test_live_security_responsibility(
    tmp_path, python_bin, openminion_root, framework_root, monkeypatch
):
    if os.environ.get("OPENMINION_LIVE_SECURITY_RESPONSIBILITY") != "1":
        pytest.skip(
            "set OPENMINION_LIVE_SECURITY_RESPONSIBILITY=1 for live finite responsibility"
        )
    assert os.environ.get("OPENMINION_LIVE_CLI_FOCUS_E2E") == "1"
    assert os.name == "posix"
    root = artifact_root(tmp_path) / "security-responsibility"
    root.mkdir(mode=0o700)
    target = root / "target"
    _fixture(target)
    (target / "app.py").write_text("def authorize(role):\n    return eval(role)\n")
    (target / "verify.py").write_text(
        "from app import authorize\nassert not authorize('True')\n"
        "assert authorize('admin')\nassert not authorize('guest')\n"
    )
    shutil.copyfile(
        os.environ["OPENMINION_TEST_SECURITY_SCAN_RULES"], target / "rule.yml"
    )
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "test: match pinned scanner authorization fixture")
    base = _git(target, "rev-parse", "HEAD")
    config_path, model, tokens, admission = _schedule_configuration(
        root, target, monkeypatch
    )
    data_root = root / "data"

    def probe(agent, stage):
        return FocusProbe(
            python_bin=python_bin,
            openminion_root=openminion_root,
            framework_root=framework_root,
            data_root=data_root,
            config_path=config_path,
            agent_id=agent,
            workdir=target,
            session_id=focus_session_id(data_root=data_root, node_name=stage),
            include_project_context=False,
            allow_unsandboxed_exec=agent == _PARENT,
        )

    parent = probe(_PARENT, "responsibility-repair")
    _install_roles(parent)
    home = Path(parent.environment()["OPENMINION_HOME"])
    command = f"{shlex.quote(str(python_bin))} -B verify.py"
    evidence = {
        "disposition": "failed",
        "stage": "daemon-start",
        "model": model,
        "source_revision": _git(openminion_root, "rev-parse", "HEAD"),
        "source_diff_sha256": sha256(
            _git(openminion_root, "diff", "HEAD").encode()
        ).hexdigest(),
        "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "max_tokens_per_turn": tokens,
        "budget_scope": "per runtime turn, not an aggregate or USD cap",
        "admission": admission.model_dump(mode="json"),
        "base_revision": base,
    }
    started = time.monotonic()
    with patch.dict(os.environ, parent.environment()):
        store = configured_cron_store(
            Namespace(
                config=str(config_path), home_root=str(home), data_root=str(data_root)
            ),
            config_ref=str(config_path),
        )
    manager = TaskManager.from_cron_repository(
        store, db_path=data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH
    )
    artifact_path = data_root / "artifact"
    try:
        with patch.dict(os.environ, parent.environment()):
            assert (
                daemon_start(str(config_path), home_root=home, data_root=data_root) == 0
            )
        evidence["initial_daemon"] = build_daemon_status_payload(
            str(config_path), home_root=home, data_root=data_root
        )
        assert evidence["initial_daemon"]["pid_alive"]
        with ArtifactCtl(
            ArtifactCtlConfig(
                blob_store=BlobStoreConfig(root_dir=str(artifact_path)),
                index=IndexConfig(sqlite_path=str(artifact_path / "index.db")),
            )
        ) as ctl:
            created = manager.schedule_user_task(
                name="finite source responsibility",
                instruction="Audit the approved source with canonical evidence",
                schedule={"kind": "every", "every_ms": 60000},
                agent_id=_RESEARCHER,
                security_scan=admission.model_dump(mode="json"),
            )
            task_id, job_id = created["record"].task_id, created["job"]["job_id"]
            reopened = TaskManager.for_lifecycle_db(
                db_path=data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH
            )
            try:
                assert (
                    reopened.get_task_by_job(job_id).metadata
                    == created["record"].metadata
                )
            finally:
                reopened.close()
            evidence.update(
                task_id=task_id, job_id=job_id, stage="initial-scheduled-scan"
            )
            surface = build_task_surface(manager, agent_id=_RESEARCHER)
            first = _wait_scan(manager, job_id)
            manager.pause_task(task_id)
            paused = surface.show_task(task_id)
            evidence["paused"] = paused
            assert paused["lifecycle_state"] == "paused" and not paused["enabled"]
            assert paused["security_scan"]["admitted_runs"] == 1
            assert all(
                run["state"] in {"finished", "cancelled"}
                for run in manager.list_scheduled_runs(job_id=job_id)
            )
            report_ref, report = _read_report(
                ctl,
                first,
                base,
                os.environ["OPENMINION_TEST_SECURITY_SCAN_IMAGE"],
                os.environ["OPENMINION_TEST_SECURITY_LAB_DOCKER_SOCKET"],
            )
            evidence["first_run"] = first
            evidence["stage"] = "initial-review"
            review_ref, review, evidence["initial_review"] = _review(
                probe(_REVIEWER, "responsibility-initial-review"),
                root,
                ctl,
                "initial-review",
                [report_ref, *report.evidence_refs],
            )
            assert review.review_status == "accepted"
            before = subprocess.run(
                [str(python_bin), "-B", "verify.py"],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert before.returncode == 1 and "AssertionError" in before.stderr
            evidence["verification"] = {
                "command": command,
                "before_exit_code": 1,
                "before_stderr": before.stderr,
            }
            handoff = _handoff(report_ref, review_ref, review, command)
            evidence["approved_handoff"] = json.loads(handoff)
            evidence["stage"] = "approved-repair"
            evidence["project"] = _repair(parent, ctl, handoff, command)
            proof = ctl.ingest_bytes(
                Path(evidence["project"]["proof_packet_ref"]).read_bytes(),
                mime="application/json",
                original_name="repair-terminal-proof.json",
                agent_id=_PARENT,
                session_id=parent.session_id,
            )
            evidence["project"]["proof_packet_artifact_ref"] = proof.ref
            assert _git(target, "diff", "--name-only", base) == "app.py"
            subprocess.run(
                [str(python_bin), "-B", "verify.py"],
                cwd=target,
                check=True,
                timeout=120,
            )
            evidence["verification"]["after_exit_code"] = 0
            _git(target, "add", "app.py")
            _git(
                target,
                "commit",
                "-qm",
                "fix: apply independently reviewed authorization repair",
            )
            repaired = _git(target, "rev-parse", "HEAD")
            assert repaired != base and not _git(target, "status", "--porcelain")
            evidence["patched_revision"] = repaired
            evidence["stage"] = "daemon-restart"
            assert (
                daemon_stop(str(config_path), home_root=home, data_root=data_root) == 0
            )
            evidence["stopped_daemon"] = build_daemon_status_payload(
                str(config_path), home_root=home, data_root=data_root
            )
            assert not evidence["stopped_daemon"]["pid_alive"]
            with patch.dict(os.environ, parent.environment()):
                assert (
                    daemon_start(str(config_path), home_root=home, data_root=data_root)
                    == 0
                )
            evidence["restarted_daemon"] = build_daemon_status_payload(
                str(config_path), home_root=home, data_root=data_root
            )
            assert evidence["restarted_daemon"]["pid_alive"]
            assert (
                evidence["restarted_daemon"]["pid"] != evidence["initial_daemon"]["pid"]
            )
            restarted = surface.show_task(task_id)
            for key in (
                "max_runs",
                "admitted_runs",
                "remaining_runs",
                "expires_at",
                "last_admitted_run_id",
                "last_successful_evidence",
            ):
                assert restarted["security_scan"][key] == paused["security_scan"][key]
            assert manager.get_scheduled_job(job_id)["payload"][
                "security_scan"
            ] == admission.model_dump(mode="json")
            assert restarted["lifecycle_state"] == "paused" and not restarted["enabled"]
            evidence["after_restart"] = restarted
            manager.resume_task(task_id)
            evidence["resumed"] = surface.show_task(task_id)
            evidence["stage"] = "second-scheduled-scan"
            second = _wait_scan(manager, job_id, first["run_id"])
            rescan_ref, rescan = _read_report(
                ctl,
                second,
                repaired,
                os.environ["OPENMINION_TEST_SECURITY_SCAN_IMAGE"],
                os.environ["OPENMINION_TEST_SECURITY_LAB_DOCKER_SOCKET"],
            )
            evidence["second_run"] = second
            evidence["stage"] = "independent-follow-up"
            followup_ref, followup, evidence["follow_up"] = _review(
                probe(_REVIEWER, "responsibility-follow-up"),
                root,
                ctl,
                "follow-up",
                [
                    review_ref,
                    report_ref,
                    rescan_ref,
                    *report.evidence_refs,
                    *rescan.evidence_refs,
                ],
            )
            assert followup.review_status == "resolved"
            evidence["stage"] = "terminal-silence"
            terminal = surface.show_task(task_id)
            assert not terminal["enabled"]
            assert terminal["security_scan"]["admitted_runs"] == 2
            assert terminal["security_scan"]["remaining_runs"] == 0
            runs_before = manager.list_scheduled_runs(job_id=job_id)
            time.sleep(65)
            assert manager.list_scheduled_runs(job_id=job_id) == runs_before
            assert (
                surface.show_task(task_id)["security_scan"] == terminal["security_scan"]
            )
            assert not _git(target, "status", "--porcelain")
            evidence.update(
                terminal=terminal,
                silence_seconds=65,
                report_ref=report_ref,
                review_ref=review_ref,
                rescan_ref=rescan_ref,
                followup_ref=followup_ref,
            )
            evidence["daemon_stop_exit_code"] = daemon_stop(
                str(config_path), home_root=home, data_root=data_root
            )
            assert evidence["daemon_stop_exit_code"] == 0
            events = _events(data_root / "telemetry/telemetry.db", 0)
            evidence.update(
                _observed_phase_usage(
                    events, json.loads(config_path.read_text())["agents"]
                )
            )
            assert (
                sha256(config_path.read_bytes()).hexdigest()
                == evidence["config_sha256"]
            )
            evidence["disposition"] = "passed"
            receipt = ctl.ingest_bytes(
                json.dumps(evidence, sort_keys=True).encode(),
                mime="application/json",
                original_name="security-responsibility-receipt.json",
                label="Finite source responsibility evidence",
                agent_id=_PARENT,
                session_id=parent.session_id,
                meta={"kind": "security-responsibility-evidence", "task_id": task_id},
            )
            for ref in (
                receipt.ref,
                proof.ref,
                evidence["project"]["accepted_child_ref"],
                report_ref,
                review_ref,
                rescan_ref,
                followup_ref,
                first["output"]["security_scan"]["sarif_ref"],
                second["output"]["security_scan"]["sarif_ref"],
                *report.evidence_refs,
                *rescan.evidence_refs,
            ):
                ctl.ref_add("collection", "security-responsibility:" + task_id, ref)
            assert json.loads(ctl.read_bytes(receipt.ref))["disposition"] == "passed"
            evidence["receipt_ref"] = receipt.ref
    except BaseException:
        evidence["disposition"] = "failed"
        raise
    finally:
        try:
            if evidence.get("daemon_stop_exit_code") != 0:
                evidence["daemon_stop_exit_code"] = daemon_stop(
                    str(config_path), home_root=home, data_root=data_root
                )
            if evidence["daemon_stop_exit_code"] != 0:
                evidence["disposition"] = "failed"
            assert evidence["daemon_stop_exit_code"] == 0
        finally:
            manager.close()
            store.close()
            evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
