from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.flagship_differentiation_proof import (
    DEFAULT_FLAGSHIP_INPUT,
    run_flagship_differentiation_proof,
)


def test_flagship_proof_replay_contains_expected_substrings(tmp_path: Path) -> None:
    artifact_path = tmp_path / "flagship-proof.json"
    result = run_flagship_differentiation_proof(
        user_input=DEFAULT_FLAGSHIP_INPUT,
        output_path=artifact_path,
    )

    for expected in (
        "Preference remembered:",
        "repo-analyst",
        "RuntimeGraph",
        "Evidence packet:",
    ):
        assert expected in result.final_answer

    assert artifact_path.exists()


def test_flagship_proof_artifact_separates_memory_and_third_brain(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "flagship-proof.json"
    result = run_flagship_differentiation_proof(output_path=artifact_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert result.artifact_path == artifact_path
    assert payload["proof_mode"] == "deterministic-integration"
    assert payload["claim_calibration"]["model_facing"] is False
    assert payload["memory"]["provenance"]["owner"] == "sophiagraph-second-brain"
    assert payload["provider"]["provenance"]["owner"] == "pragmagraph-third-brain"
    assert payload["delegation"]["surface"] == "service-level-brain-delegate-mode"
    assert payload["delegation"]["action_result"]["status"] == "success"
    assert payload["provider"]["source_ref"]["path"] == "src/app.py"
    assert (
        "keep answers terse and cite file paths" in payload["memory"]["content"].lower()
    )
    assert "## Third-brain graph context" in payload["integrated_context"]["body"]
    assert (
        payload["integrated_context"]["metadata"]["context_knowledge_graph"] == "true"
    )
