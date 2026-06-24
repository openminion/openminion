from __future__ import annotations

from pathlib import Path

from tests.memory.runtime.tmc_closeout_smoke import (
    run_closeout_smoke,
    write_closeout_artifact,
)


def test_tmc_closeout_smoke_checks_all_required_conditions(tmp_path: Path) -> None:
    artifact = run_closeout_smoke(artifact_root=tmp_path / "tmc-closeout")

    assert artifact.merge_model_name == "gpt-4.2-mini"
    assert artifact.merge_model_override_used is True
    assert all(artifact.checks.values())
    assert artifact.promoted_record_ids
    assert artifact.blocked_errors
    assert artifact.superseded_valid_to


def test_tmc_closeout_smoke_writes_summary_json(tmp_path: Path) -> None:
    artifact_root = tmp_path / "tmc-closeout"
    artifact = run_closeout_smoke(artifact_root=artifact_root)
    out_path = write_closeout_artifact(artifact, artifact_root)

    assert out_path == artifact_root / "summary.json"
    payload = out_path.read_text(encoding="utf-8")
    assert '"merge_model_name": "gpt-4.2-mini"' in payload
    assert '"maintenance_marker_updated": true' in payload
