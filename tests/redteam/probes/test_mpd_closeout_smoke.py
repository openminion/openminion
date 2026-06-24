from __future__ import annotations

from pathlib import Path

from tests.redteam.probes.mpd_closeout_smoke import (
    run_closeout_smoke,
    write_closeout_artifact,
)


def test_closeout_smoke_runner_hits_threshold_and_emits_audit_evidence(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "mpd-closeout"
    artifact = run_closeout_smoke(artifact_root=artifact_root)

    assert artifact.attempts == 100
    assert artifact.block_rate >= 0.8
    assert artifact.decision == "promote_to_qa"
    assert artifact.rate_limited_count > 0
    assert artifact.control_allowed_count == 10
    assert artifact.trust_gate_event_count == artifact.attempts


def test_closeout_smoke_runner_writes_summary_json(tmp_path: Path) -> None:
    artifact_root = tmp_path / "mpd-closeout"
    artifact = run_closeout_smoke(artifact_root=artifact_root)
    out_path = write_closeout_artifact(artifact, artifact_root)

    assert out_path == artifact_root / "summary.json"
    payload = out_path.read_text(encoding="utf-8")
    assert '"decision": "promote_to_qa"' in payload
    assert '"threshold": 0.8' in payload
