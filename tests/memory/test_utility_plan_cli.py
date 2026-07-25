from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from openminion.modules.memory.cli import _build_app, _get_service
from openminion.modules.memory.storage.base import CandidateListOptions


def _write_canary(
    path: Path, *, report_version: str = "memory-context-operational-canary.v1"
) -> None:
    path.write_text(
        json.dumps(
            {
                "report_version": report_version,
                "generated_at": "1970-01-01T00:00:00Z",
                "run_id": "canary-run",
                "summary": {"all_passed": False, "case_count": 2},
                "metadata": {"source": "test"},
                "cases": [
                    {
                        "case_id": "case-memory",
                        "task_type": "memory",
                        "status": "fail",
                        "scorecard_metric": "memory_influence",
                        "score": 0.2,
                        "threshold": 0.7,
                        "memory_enabled": True,
                        "blocks_enabled": False,
                        "session_length_bucket": "short",
                        "context_budget_policy": "default",
                        "evidence_refs": ["trace://memory/1"],
                        "source_trace_refs": ["ctx-1"],
                        "redaction_status": "redacted",
                    },
                    {
                        "case_id": "case-permission",
                        "task_type": "governance",
                        "status": "fail",
                        "scorecard_metric": "permission_safety",
                        "score": 0.1,
                        "threshold": 1.0,
                        "memory_enabled": True,
                        "blocks_enabled": True,
                        "session_length_bucket": "short",
                        "context_budget_policy": "default",
                        "evidence_refs": ["trace://memory/2"],
                        "source_trace_refs": ["ctx-2"],
                        "redaction_status": "redacted",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_utility_plan_dry_run_does_not_stage_candidates(tmp_path: Path) -> None:
    canary = tmp_path / "canary.json"
    db = tmp_path / "memory.db"
    _write_canary(canary)
    runner = CliRunner()

    result = runner.invoke(
        _build_app(),
        ["review", "utility-plan", "--canary", str(canary), "--db", str(db), "--json"],
    )

    payload = json.loads(result.output)
    candidates = _get_service(str(db)).candidate_list(CandidateListOptions())
    assert result.exit_code == 0, result.output
    assert payload["schema_version"] == "memory-utility-plan.v1"
    assert payload["dry_run"] is True
    assert payload["staged"] is False
    assert [item["disposition"] for item in payload["items"]] == [
        "review_demote",
        "review_forget",
    ]
    assert candidates == []


def test_utility_plan_stage_writes_review_candidates(tmp_path: Path) -> None:
    canary = tmp_path / "canary.json"
    db = tmp_path / "memory.db"
    _write_canary(canary)
    runner = CliRunner()

    result = runner.invoke(
        _build_app(),
        [
            "review",
            "utility-plan",
            "--canary",
            str(canary),
            "--db",
            str(db),
            "--stage",
            "--json",
        ],
    )

    payload = json.loads(result.output)
    candidates = _get_service(str(db)).candidate_list(
        CandidateListOptions(proposed_scope="session:canary-run")
    )
    assert result.exit_code == 0, result.output
    assert payload["dry_run"] is False
    assert payload["staged"] is True
    assert len(candidates) == 2
    assert candidates[0].status == "proposed"
    assert candidates[0].meta["source_scorecard_run_id"] == "canary-run"
    assert "source_artifact_sha256" in candidates[0].meta


def test_utility_plan_rejects_wrong_schema_version(tmp_path: Path) -> None:
    canary = tmp_path / "canary.json"
    _write_canary(canary, report_version="wrong.v1")

    result = CliRunner().invoke(
        _build_app(),
        ["review", "utility-plan", "--canary", str(canary), "--json"],
    )

    assert result.exit_code == 1
    assert "unsupported canary report_version" in result.output


def test_utility_plan_rejects_missing_evidence(tmp_path: Path) -> None:
    canary = tmp_path / "canary.json"
    _write_canary(canary)
    payload = json.loads(canary.read_text(encoding="utf-8"))
    payload["cases"][0]["evidence_refs"] = []
    canary.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(
        _build_app(),
        ["review", "utility-plan", "--canary", str(canary), "--json"],
    )

    assert result.exit_code == 1
    assert "evidence_refs are required" in result.output
