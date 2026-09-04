from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.ollama_fixture import ollama_fixture_server

pytestmark = pytest.mark.e2e


def _fixture_config(path: Path, base_url: str) -> None:
    path.write_text(
        json.dumps(
            {
                "default_agent": "fixture",
                "agents": {
                    "fixture": {
                        "name": "fixture",
                        "provider": "ollama",
                        "model": "qwen2.5:14b",
                    }
                },
                "providers": {
                    "ollama": {
                        "model": "qwen2.5:14b",
                        "base_url": base_url,
                    }
                },
                "runtime": {"demo_mode": False},
            }
        ),
        encoding="utf-8",
    )


def test_workspace_trust_fresh_project(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    framework_root: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    shared = tmp_path / "shared"
    project.mkdir()
    shared.mkdir()
    shared_target = str((shared / "shared.txt").resolve())
    responses = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "list",
                    "function": {
                        "name": "file.list_dir",
                        "arguments": {"path": "."},
                    },
                },
                {
                    "id": "write",
                    "function": {
                        "name": "file.write",
                        "arguments": {"path": "created.txt", "content": "draft"},
                    },
                },
                {
                    "id": "shared-write",
                    "function": {
                        "name": "file.write",
                        "arguments": {"path": shared_target, "content": "shared"},
                    },
                },
                {
                    "id": "exec",
                    "function": {
                        "name": "exec.run",
                        "arguments": {"command": "pwd"},
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "edit",
                    "function": {
                        "name": "file.edit",
                        "arguments": {
                            "path": "created.txt",
                            "operations": [
                                {
                                    "op": "replace",
                                    "old_text": "draft",
                                    "new_text": "verified",
                                }
                            ],
                        },
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": (
                "WORKSPACE_TRUST_OK; files edited and command posture checked.\n\n"
                '<finalization_status>{"status":"final_answer",'
                '"reasoning":"Workspace checks completed."}</finalization_status>'
            ),
        },
    )

    with ollama_fixture_server(responses) as (base_url, requests):
        config = tmp_path / "config.json"
        _fixture_config(config, base_url)
        monkeypatch.setenv("OLLAMA_API_KEY", "fixture-key-not-for-network-use")
        probe = FocusProbe(
            python_bin=python_bin,
            openminion_root=openminion_root,
            framework_root=framework_root,
            data_root=tmp_path / "data",
            config_path=config,
            agent_id="fixture",
            workdir=project,
            session_id="workspace-trust-local",
            allow_unsandboxed_exec=True,
            added_dirs=(shared,),
        )
        scenario = FocusScenario(
            scenario_id="workspace-trust-local",
            prompt="Inspect and update the current project and validate command posture.",
            expected_markers=("WORKSPACE_TRUST_OK",),
            timeout=180,
            requires_approval=True,
            approval_reply="session",
        )
        with probe.session(rows=50, cols=160) as session:
            probe.wait_ready(session)
            transcript = probe.run_turn(session, scenario)

    assert (project / "created.txt").read_text(encoding="utf-8") == "verified"
    assert (shared / "shared.txt").read_text(encoding="utf-8") == "shared"
    assert str(project.resolve()) in transcript
    assert "fixture-key-not-for-network-use" not in transcript
    assert "openminion_ephemeral_workspace_roots" not in transcript
    assert requests


@pytest.mark.timeout(2700)
@pytest.mark.parametrize("run_index", (1, 2))
def test_live_workspace_trust_fresh_project(
    focus_probe: FocusProbe,
    tmp_path: Path,
    run_index: int,
) -> None:
    require_live_focus()
    project = tmp_path / f"project-{run_index}"
    shared = tmp_path / f"shared-{run_index}"
    project.mkdir()
    shared.mkdir()
    probe = focus_probe.for_workdir(project, include_project_context=False)
    probe.added_dirs = (shared,)
    scenario = FocusScenario(
        scenario_id=f"workspace-trust-live-{run_index}",
        prompt=(
            "Request any needed tool schemas together. In one tool batch, use "
            "structured file tools to create `verified.txt` in the current directory "
            "with content `verified` and create `shared.txt` in the explicitly added "
            f"directory `{shared.resolve()}` with content `shared`; also run one `pwd` "
            "command and respect any approval. Treat successful structured tool "
            "results as validation and do not re-read the files. Then include "
            "WORKSPACE_TRUST_LIVE_OK in the final answer and emit the typed completion "
            "trailers required by the system instructions."
        ),
        expected_markers=(
            "WORKSPACE_TRUST_LIVE_OK|"
            "tool or model budget was exhausted before a polished final answer",
        ),
        timeout=1200,
        requires_approval=True,
        max_auto_approvals=5,
        approval_reply="session",
    )

    with probe.session(rows=55, cols=180) as session:
        probe.wait_ready(session)
        transcript = probe.run_turn(session, scenario)

    assert (project / "verified.txt").read_text(encoding="utf-8") == "verified"
    assert (shared / "shared.txt").read_text(encoding="utf-8") == "shared"
    assert str(project.resolve()) in transcript
    assert str(shared.resolve()) in transcript
    assert "openminion_ephemeral_workspace_roots" not in transcript
