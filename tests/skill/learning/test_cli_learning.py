from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.modules.skill.cli import main
from openminion.modules.skill.learning.shapes import WorkflowShape, command_fingerprint


def _config_path(tmp_path: Path) -> Path:
    db = tmp_path / "skill.db"
    cfg = tmp_path / "skill.json"
    cfg.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(db),
                    "blob_root": str(tmp_path / "blob"),
                    "fallback_root": str(tmp_path / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _run_cli(argv: list[str]) -> dict[str, object]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0, f"CLI exit code: {rc}; stdout={buf.getvalue()!r}"
    return json.loads(buf.getvalue())


def _run_cli_expect_failure(argv: list[str]) -> dict[str, object]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit):
            main(argv)
    return json.loads(buf.getvalue())


def _shape() -> WorkflowShape:
    return WorkflowShape(
        intent_category="task:test_cleanup",
        capability_category="capability:cleanup",
        strategy_id="strategy:test_cleanup",
        tool_names=["exec"],
        command_fingerprints=[command_fingerprint(("pytest", "tests"))],
        test_fingerprints=[command_fingerprint(("ruff", "check", "."))],
        artifact_types=["md"],
        success_count=2,
        evidence_refs=["proof:1", "proof:2"],
        performance_entry_refs=["proof:1", "proof:2"],
        knowledge_record_refs=["proof:1", "proof:2"],
    )


def _write_json(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_learning_cli_scan_inspect_save_and_trust_status(tmp_path: Path) -> None:
    cfg = _config_path(tmp_path)
    shape = _shape()
    shape_path = _write_json(tmp_path, "shape.json", shape.model_dump(mode="json"))
    bundle_path = _write_json(
        tmp_path,
        "bundles.json",
        [
            {
                "source_run_refs": ["run-1"],
                "tool_names": ["exec"],
                "command_fingerprints": shape.command_fingerprints,
                "test_fingerprints": shape.test_fingerprints,
                "artifact_types": ["md"],
                "outcome": "success",
                "intent_category": shape.intent_category,
                "capability_category": shape.capability_category,
                "strategy_id": shape.strategy_id,
            },
            {
                "source_run_refs": ["run-2"],
                "tool_names": ["exec"],
                "command_fingerprints": shape.command_fingerprints,
                "test_fingerprints": shape.test_fingerprints,
                "artifact_types": ["md"],
                "outcome": "success",
                "intent_category": shape.intent_category,
                "capability_category": shape.capability_category,
                "strategy_id": shape.strategy_id,
            },
        ],
    )

    scanned = _run_cli(
        ["--config", str(cfg), "learning-scan", "--bundle-json", str(bundle_path)]
    )
    assert scanned["ok"] is True
    assert len(scanned["shapes"]) == 1

    inspected = _run_cli(
        ["--config", str(cfg), "learning-inspect", "--shape-json", str(shape_path)]
    )
    assert inspected["shape"]["shape_id"] == shape.shape_id

    saved = _run_cli(
        [
            "--config",
            str(cfg),
            "learning-save-workflow",
            "--shape-json",
            str(shape_path),
            "--actor-id",
            "operator-1",
            "--source-run-ref",
            "run-1",
        ]
    )
    assert saved["save_workflow"]["explicit_save"] is True

    trust = _run_cli(
        [
            "--config",
            str(cfg),
            "learning-trust-status",
            "--skill-id",
            "emergent.test",
            "--shape-id",
            shape.shape_id,
        ]
    )
    assert trust["trust"]["trust_state"] == "candidate"


def test_learning_cli_propose_replay_and_apply_gate(tmp_path: Path) -> None:
    cfg = _config_path(tmp_path)
    shape = _shape()
    shape_path = _write_json(tmp_path, "shape.json", shape.model_dump(mode="json"))

    proposed = _run_cli(
        ["--config", str(cfg), "learning-propose", "--shape-json", str(shape_path)]
    )
    result = proposed["result"]
    assert result["status"] == "staged"
    proposal_id = result["proposal"]["proposal_id"]

    proof = _run_cli(
        [
            "--config",
            str(cfg),
            "learning-replay-proof",
            "--proposal-id",
            proposal_id,
            "--shape-id",
            shape.shape_id,
            "--proof-id",
            "proof-1",
            "--status",
            "passed",
            "--evidence",
            "replay:1",
        ]
    )
    assert proof["proof"]["status"] == "passed"

    _run_cli(
        [
            "--config",
            str(cfg),
            "proposal-review",
            proposal_id,
            "--reviewer-id",
            "operator-cli",
            "--criterion",
            "fit:accepted:recurring evidence",
        ]
    )

    failed = _run_cli_expect_failure(
        [
            "--config",
            str(cfg),
            "learning-apply-proved",
            "--proposal-id",
            proposal_id,
            "--shape-id",
            shape.shape_id,
            "--proof-id",
            "proof-2",
            "--proof-status",
            "failed",
        ]
    )
    assert failed["ok"] is False
    assert failed["error"]["code"] == "INVALID_ARGUMENT"

    applied = _run_cli(
        [
            "--config",
            str(cfg),
            "learning-apply-proved",
            "--proposal-id",
            proposal_id,
            "--shape-id",
            shape.shape_id,
            "--proof-id",
            "proof-3",
            "--proof-status",
            "passed",
        ]
    )
    assert applied["addition"]["added_skill_id"].startswith("emergent.")
