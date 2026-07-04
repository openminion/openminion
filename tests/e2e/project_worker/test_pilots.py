from __future__ import annotations

import json

from tests.e2e.project_worker.harness import (
    ProjectWorkerScenarioKind,
    build_project_pilot_report,
    default_pilot_specs,
    scenarios_for_suite,
    soak_pilot_specs,
    write_project_pilot_artifacts,
)


def test_project_worker_pilot_scenarios_are_registered() -> None:
    pilots = scenarios_for_suite("pilot")
    soak = scenarios_for_suite("pilot-24h-restart-resume")

    assert {pilot.scenario_id for pilot in pilots} == {
        "pilot-30m-local",
        "pilot-2h-coding-research",
    }
    assert {pilot.kind for pilot in pilots} == {ProjectWorkerScenarioKind.PILOT}
    assert soak[0].kind == ProjectWorkerScenarioKind.SOAK


def test_project_worker_pilot_reports_include_required_evidence() -> None:
    reports = {
        spec.pilot_id: build_project_pilot_report(spec)
        for spec in default_pilot_specs()
    }

    short = reports["pilot-30m-local"]
    medium = reports["pilot-2h-coding-research"]

    assert short.metrics.proof_packet_completeness_percent == 100.0
    assert short.metrics.restart_resume_success_count == 1
    assert short.proof_refs
    assert short.ux_notes
    assert medium.metrics.verification_pass_count >= 5
    assert medium.metrics.operator_intervention_count == 2
    assert medium.safety_notes
    assert medium.capability_matrix is not None


def test_project_worker_soak_pilot_proves_restart_resume_and_duplicate_guard() -> None:
    spec = {
        pilot.pilot_id: pilot for pilot in soak_pilot_specs()
    }["pilot-24h-restart-resume"]
    report = build_project_pilot_report(spec)

    assert spec.pilot_id == "pilot-24h-restart-resume"
    assert report.metrics.restart_resume_attempt_count == 3
    assert report.metrics.restart_resume_success_count == 3
    assert report.metrics.proof_packet_completeness_percent == 100.0
    assert "duplicate worker: rejected" in report.ux_notes
    assert report.proof_refs


def test_project_worker_multiday_pilot_has_operator_blocked_and_verification_gate() -> None:
    spec = {pilot.pilot_id: pilot for pilot in soak_pilot_specs()}["pilot-72h-multiday"]
    report = build_project_pilot_report(spec)

    assert report.metrics.restart_resume_attempt_count == 5
    assert report.metrics.restart_resume_success_count == 5
    assert report.metrics.stale_evidence_invalidation_count == 1
    assert report.metrics.retry_count == 2
    assert report.metrics.blocked_duration_ms > 0
    assert report.metrics.operator_intervention_count == 3
    assert "operator decision: resolved" in report.ux_notes
    assert "blocked retry: recovered" in report.ux_notes
    assert (
        "claim status: no unqualified days-or-longer claim from compressed proof"
        in report.ux_notes
    )


def test_project_worker_pilot_artifact_writer_outputs_json_markdown_and_status(
    tmp_path,
) -> None:
    artifacts = write_project_pilot_artifacts(tmp_path)

    assert {artifact.pilot_id for artifact in artifacts} == {
        "pilot-30m-local",
        "pilot-2h-coding-research",
    }
    for artifact in artifacts:
        payload = json.loads(artifact.json_path.read_text(encoding="utf-8"))
        markdown = artifact.markdown_path.read_text(encoding="utf-8")
        status = artifact.status_path.read_text(encoding="utf-8")

        assert payload["report"]["metrics"]["proof_packet_completeness_percent"] == 100.0
        assert payload["report"]["proof_refs"]
        assert payload["operator_status"]
        assert "compressed deterministic pilot artifact" in markdown
        assert "status:" in status
