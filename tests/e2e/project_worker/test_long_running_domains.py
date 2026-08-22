from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.cli.main import main


_FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.e2e


def _run_project(
    tmp_path: Path,
    monkeypatch,
    *,
    domain: str,
    turn,
    verify_command: str,
) -> dict[str, object]:
    monkeypatch.setattr("openminion.cli.commands.autonomy_project.run_turn", turn)
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(
            [
                "--home-root",
                str(tmp_path / "home"),
                "--data-root",
                str(tmp_path / "data"),
                "--no-interactive",
                "autonomy",
                "start",
                "--goal",
                f"Produce the verified {domain} artifact",
                "--workspace",
                str(tmp_path),
                "--verification-domain",
                domain,
                "--max-iterations",
                "2",
                "--verify-command",
                verify_command,
                "--json",
            ]
        )
    assert code == 0
    return json.loads(output.getvalue())["run"]


def test_frozen_research_project_preserves_sources_and_conflict(
    tmp_path, monkeypatch
) -> None:
    manifest_path = tmp_path / "research_manifest.json"
    manifest_path.write_bytes((_FIXTURES / "research_manifest.json").read_bytes())
    report_path = tmp_path / "research_report.json"

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report_path.write_text(
            json.dumps(
                {
                    "as_of_date": manifest["corpus_date"],
                    "source_ledger": manifest["sources"],
                    "claim_ledger": [
                        {
                            "claim": "bounded local execution support is disputed",
                            "supporting_source_ids": ["source-alpha"],
                            "contradicting_source_ids": ["source-beta"],
                            "disposition": "unresolved_conflict",
                        }
                    ],
                    "unavailable_source_ids": ["source-gamma"],
                }
            ),
            encoding="utf-8",
        )
        return {
            "final_text": "research ledger written",
            "metadata": {
                "artifact_refs": [f"file:{report_path.name}"],
                "evidence_kinds": ["artifact"],
            },
        }

    verify = " ".join(
        (
            shlex.quote(sys.executable),
            shlex.quote(str(_FIXTURES / "verify_research.py")),
            shlex.quote(str(manifest_path)),
            shlex.quote(str(report_path)),
        )
    )
    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="research",
        turn=turn,
        verify_command=verify,
    )

    assert run["status"] == "completed"
    assert report_path.exists()


def test_cross_application_project_uses_same_worker_and_typed_verifier(
    tmp_path,
    monkeypatch,
) -> None:
    handoff_path = tmp_path / "handoff.json"

    def turn(*, config_path, payload):  # noqa: ANN001, ARG001
        handoff_path.write_text(
            json.dumps(
                {
                    "schema_version": "project-handoff.v1",
                    "title": "Release handoff",
                    "owners": ["engineering", "operations"],
                    "items": [
                        {
                            "name": "tests",
                            "status": "ready",
                            "source_ref": "proof:tests",
                        },
                        {
                            "name": "deployment",
                            "status": "blocked",
                            "source_ref": "decision:approval",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return {
            "final_text": "handoff written",
            "metadata": {
                "artifact_refs": [f"file:{handoff_path.name}"],
                "evidence_kinds": ["artifact"],
            },
        }

    verify = " ".join(
        (
            shlex.quote(sys.executable),
            shlex.quote(str(_FIXTURES / "verify_handoff.py")),
            shlex.quote(str(handoff_path)),
        )
    )
    run = _run_project(
        tmp_path,
        monkeypatch,
        domain="cross_application",
        turn=turn,
        verify_command=verify,
    )

    assert run["status"] == "completed"
    assert handoff_path.exists()


def test_research_verifier_rejects_invented_source(tmp_path) -> None:
    manifest_path = tmp_path / "research_manifest.json"
    manifest_path.write_bytes((_FIXTURES / "research_manifest.json").read_bytes())
    report_path = tmp_path / "research_report.json"
    report_path.write_text(
        json.dumps(
            {
                "as_of_date": "2026-08-15",
                "source_ledger": [{"source_id": "invented"}],
                "claim_ledger": [{}],
                "unavailable_source_ids": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_FIXTURES / "verify_research.py"),
            str(manifest_path),
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
