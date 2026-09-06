from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


_MARKDOWN = """---
name: operational-reuse-proof
description: Create a deterministic operational reuse proof artifact.
verification:
  - Confirm reuse-proof.json contains the expected marker.
---
# Operational Reuse Proof

## Procedure

Write `reuse-proof.json` with `{"marker": "skill-operational-reuse"}`.
"""


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(*args: str, cwd: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_proposed_skill_is_selected_and_rendered_across_processes(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[3]
    config_path = tmp_path / "skill.json"
    db_path = tmp_path / "skill.db"
    config_path.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(db_path),
                    "blob_root": str(tmp_path / "blob"),
                    "fallback_root": str(tmp_path / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )

    propose_script = """
import json
import sys
from types import SimpleNamespace
from openminion.modules.skill.runtime.skill import Skill
from openminion.tools.skill.plugin import _h_skill_propose

skill = Skill(sys.argv[1])
try:
    result = _h_skill_propose(
        {"skill_markdown": sys.argv[2]},
        SimpleNamespace(
            skill_api=skill,
            session_id="authoring-session",
            run_id="authoring-run",
            trace_id="authoring-trace",
        ),
    )
    print(json.dumps(result))
finally:
    skill.close()
"""
    proposed = _run(
        "-c",
        propose_script,
        str(config_path),
        _MARKDOWN,
        cwd=repo,
    )
    assert proposed["ok"] is True
    proposal_id = str(proposed["proposal_id"])

    cli = ("-m", "openminion.modules.skill.cli", "--config", str(config_path))
    reviewed = _run(
        *cli,
        "proposal-review",
        proposal_id,
        "--criterion",
        "fit:accepted:bounded reusable procedure",
        cwd=repo,
    )
    assert reviewed["review"]["status"] == "accepted"

    evidence_path = tmp_path / "verification.txt"
    evidence_path.write_text("passed: deterministic artifact contract\n", encoding="utf-8")
    verified = _run(
        *cli,
        "proposal-verify",
        proposal_id,
        "--check",
        "artifact-contract",
        "--evidence-ref",
        str(evidence_path),
        cwd=repo,
    )
    assert verified["verification_evidence"]["result"] == "passed"

    applied = _run(*cli, "proposal-apply", proposal_id, cwd=repo)
    addition = applied["addition"]
    assert addition["added_skill_id"] == "operational_reuse_proof"

    reuse_script = """
import json
import sys
from types import SimpleNamespace
from openminion.modules.brain.bootstrap.skill.hints import resolve_skill_hints
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.context.schemas import BuildConstraints, BuildPackRequest, IdentitySnippet, SessionSlice
from openminion.modules.context.service import ContextCtlService
from openminion.modules.skill.runtime.skill import Skill

class SessionAPI:
    def get_slice(self, **kwargs):
        return {"recent_turns": [], "open_tasks": [], "recent_tool_events": [], "summary_short": ""}

class Logger:
    def emit(self, *args, **kwargs):
        return None

class Identity:
    contract_version = "v1"
    def render(self, *, agent_id, purpose, max_tokens, provider_pref=None):
        return IdentitySnippet(agent_id=agent_id, profile_version="v1", render_version="v1", text="reuse agent")

class Memory:
    contract_version = "v1"
    def query_facts(self, **kwargs): return []
    def query_memory_cards(self, **kwargs): return []
    def recall_session_start_memory(self, **kwargs): return []
    def recall_mid_session_memory(self, **kwargs): return []
    def recall_recent_session_artifacts(self, **kwargs): return []
    def get_procedure(self, **kwargs): return None

class Artifacts:
    contract_version = "v1"
    def query_digests(self, **kwargs): return []

class Sessions:
    contract_version = "v1"
    def get_slice(self, *, session_id, purpose, limits):
        return SessionSlice(session_id=session_id, slice_version="v1", summary_short="")

skill = Skill(sys.argv[1])
try:
    session_api = SessionAPI()
    runner = SimpleNamespace(
        skill_api=skill,
        llm_api=SimpleNamespace(),
        session_api=session_api,
        profile=SimpleNamespace(
            skill=None,
            skill_catalog=[],
            llm_profiles=SimpleNamespace(act_model="", summarize_model="test-model"),
        ),
    )
    state = WorkingState(
        session_id="reuse-session",
        agent_id="reuse-agent",
        trace_id="reuse-trace",
        phase="ACT",
        budgets_remaining=BudgetCounters(ticks=8, tool_calls=8, a2a_calls=0, tokens=1000, time_ms=60000),
    )
    hints = resolve_skill_hints(
        runner,
        intent="Use the exact named skill operational_reuse_proof.",
        purpose="act",
        state=state,
        logger=Logger(),
    )
    service = ContextCtlService(
        identityctl=Identity(),
        sessctl=Sessions(),
        memctl=Memory(),
        artifactctl=Artifacts(),
        skillctl=skill,
    )
    pack = service.build_pack(BuildPackRequest(
        session_id=state.session_id,
        agent_id=state.agent_id,
        purpose="act",
        query="Use the exact named skill operational_reuse_proof.",
        constraints=BuildConstraints(skill_refs=hints["skill_refs"]),
    ))
    rendered = "\\n".join(str(message.content) for message in pack.messages)
    assert "skill-operational-reuse" in rendered
    print(json.dumps({
        "skill_id": hints["skill_id"],
        "version_hash": hints["skill_version_hash"],
        "context_contains_marker": True,
    }))
finally:
    skill.close()
"""
    reused = _run(
        "-c",
        reuse_script,
        str(config_path),
        cwd=repo,
    )
    assert reused["skill_id"] == "operational_reuse_proof"
    assert reused["context_contains_marker"] is True
