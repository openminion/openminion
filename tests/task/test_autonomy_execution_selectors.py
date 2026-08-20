from __future__ import annotations

import json

from openminion.modules.task import AutonomyRunStore, build_autonomy_run


def test_autonomy_selectors_survive_restart_without_secret_values(tmp_path) -> None:
    store = AutonomyRunStore(root=tmp_path / "autonomy")
    run = build_autonomy_run(
        goal_text="Build and verify the artifact",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=3,
        permission_profile_id="local-safe",
        agent_id="coding-agent",
        config_ref="profiles/minimax.json",
        default_act_profile="coding",
        verification_domain="coding",
        verifier_ref="command",
        verification_commands=("pytest -q",),
        required_evidence_kinds=("tests", "diff"),
    )

    store.create(run)
    loaded = AutonomyRunStore(root=tmp_path / "autonomy").require(run.run_id)
    persisted = json.loads(
        (tmp_path / "autonomy" / "runs" / f"{run.run_id}.json").read_text()
    )

    assert loaded.execution_selectors == run.execution_selectors
    assert loaded.execution_selectors.agent_id == "coding-agent"
    assert loaded.execution_selectors.verification_domain == "coding"
    assert "api_key" not in json.dumps(persisted).lower()
    assert not list((tmp_path / "autonomy" / "runs").glob(".*.json.*"))


def test_legacy_autonomy_record_uses_compatible_selector_defaults(tmp_path) -> None:
    store = AutonomyRunStore(root=tmp_path / "autonomy")
    run = build_autonomy_run(
        goal_text="Legacy run",
        goal_id="goal-1",
        session_id="session-1",
        workspace_ref="local:/workspace#commit=abc;dirty=clean",
        max_iterations=1,
    )
    payload = run.model_dump(mode="json")
    payload.pop("execution_selectors")
    path = tmp_path / "autonomy" / "runs" / f"{run.run_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.require(run.run_id)

    assert loaded.execution_selectors.agent_id == "default"
    assert loaded.execution_selectors.verification_domain == "cross_application"
