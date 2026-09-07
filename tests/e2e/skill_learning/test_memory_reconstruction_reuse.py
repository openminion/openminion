from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


_NONCE = "memory-only-proof-7319"


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


def test_completed_work_memory_reconstructs_and_reuses_skill_across_processes(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[3]
    memory_db = tmp_path / "memory.db"
    skill_db = tmp_path / "skill.db"
    skill_config = tmp_path / "skill.json"
    skill_config.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(skill_db),
                    "blob_root": str(tmp_path / "blob"),
                    "fallback_root": str(tmp_path / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )

    stage_script = r"""
import json
import sys
from types import SimpleNamespace
from openminion.modules.brain.adapters.memory import MemctlAdapter
from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.runtime.memory import apply_success_memories
from openminion.modules.brain.schemas import BudgetCounters, SuccessMemoryConfig, SuccessMemoryReport, WorkingState
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore

class Logger:
    def emit(self, *args, **kwargs):
        return None

store = SQLiteMemoryStore(sys.argv[1])
service = MemoryService(store=store)
config = SuccessMemoryConfig(enabled=True, max_items_per_turn=1)
runner = SimpleNamespace(
    profile=SimpleNamespace(agent_id="memory-skill-agent", success_memory=config),
    options=RunnerOptions(success_memory_config=config),
    memory_api=MemctlAdapter(service, agent_id="memory-skill-agent"),
)
state = WorkingState(
    session_id="completed-work-session",
    agent_id="memory-skill-agent",
    trace_id="completed-work-trace",
    budgets_remaining=BudgetCounters(ticks=1, tool_calls=1, a2a_calls=0, tokens=100, time_ms=1000),
)
report = SuccessMemoryReport.model_validate({
    "session_id": state.session_id,
    "agent_id": state.agent_id,
    "items": [{
        "kind": "procedure",
        "title": "Reconstruct the memory proof report",
        "content": {
            "steps": [
                "Write memory-reconstruction-proof.json as a JSON object with marker set to " + sys.argv[2],
                "Read the file back and confirm the marker matches exactly",
            ],
            "validation": ["The JSON marker must match the remembered marker"],
            "rollback_hint": "Remove the generated report if validation fails",
            "proof_nonce": sys.argv[2],
            "tools": ["file.write", "file.read"],
        },
        "confidence": 0.95,
        "rationale": "The completed workflow was successful and reusable",
        "tags": ["memory-reconstruction"],
    }],
})
result = apply_success_memories(
    runner,
    state=state,
    report=report,
    logger=Logger(),
    provenance_meta={"source_run_id": "completed-work-run"},
)
print(json.dumps(result))
store.close()
"""
    staged = _run("-c", stage_script, str(memory_db), _NONCE, cwd=repo)
    candidate_ids = list(staged["candidate_ids"])
    assert len(candidate_ids) == 1

    promote_script = r"""
import json
import sys
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore

store = SQLiteMemoryStore(sys.argv[1])
service = MemoryService(store=store)
candidate = service.candidate_get(sys.argv[2])
assert candidate.type == "procedure"
assert candidate.status == "proposed"
service.candidate_update(candidate.candidate_id, {"status": "approved"})
record = service.promote_candidate(candidate.candidate_id, "agent:memory-skill-agent")
print(json.dumps({"record_id": record.id, "scope": record.scope, "content": record.content}))
store.close()
"""
    promoted = _run("-c", promote_script, str(memory_db), candidate_ids[0], cwd=repo)
    assert promoted["scope"] == "agent:memory-skill-agent"
    assert promoted["content"]["proof_nonce"] == _NONCE
    assert promoted["content"]["tools"] == ["file.write", "file.read"]

    author_script = r'''
import json
import sys
from types import SimpleNamespace
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.memory import REGISTRAR as MEMORY_REGISTRAR
from openminion.tools.skill.plugin import _h_skill_propose

memory_store = SQLiteMemoryStore(sys.argv[1])
memory_service = MemoryService(store=memory_store)
registry = ToolRegistry()
MEMORY_REGISTRAR.register(registry)
search = registry.execute_calls(
    [ProviderToolCall(
        name="memory.search",
        arguments={
            "query": "reconstruct memory proof report",
            "scopes": ["agent:memory-skill-agent"],
            "types": ["procedure"],
            "limit": 1,
        },
        id="search-procedure",
        source="deterministic-model",
    )],
    context=ToolExecutionContext(
        channel="console",
        target="cli-chat",
        session_id="memory-author-session",
        memory_service=memory_service,
    ),
).results[0]
assert search.ok
record = search.data["records"][0]
nonce = record["content"]["proof_nonce"]
steps = record["content"]["steps"]
tools = record["content"]["tools"]
tool_lines = "\n".join(f"  - {tool}" for tool in tools)
markdown = f"""---
name: memory-reconstruction-proof
description: Recreate and verify a report from remembered procedure steps.
tools:
{tool_lines}
verification:
  - Confirm memory-reconstruction-proof.json contains the remembered marker.
---
# Memory Reconstruction Proof

## Procedure

1. {steps[0]}
2. {steps[1]}

## Validation

The exact remembered marker is `{nonce}`.
"""
skill = Skill(sys.argv[2])
try:
    assert skill.store.list_proposals(queue_state="pending", limit=10) == []
    assert skill.store.list_skills() == []
    proposal = _h_skill_propose(
        {"skill_markdown": markdown},
        SimpleNamespace(
            skill_api=skill,
            session_id="memory-author-session",
            run_id="memory-author-run",
            trace_id="memory-author-trace",
        ),
    )
    row = skill.store.get_proposal(proposal_id=proposal["proposal_id"])
    print(json.dumps({
        "search_count": search.data["count"],
        "search_nonce": nonce,
        "proposal": proposal,
        "proposal_tools": row["proposal"]["proposed_skill_definition"]["tools"],
        "evidence_refs": row["proposal"]["evidence_refs"],
        "queue_state": row["queue_state"],
        "markdown": row["proposal"]["skill_markdown"],
        "catalog_count": len(skill.store.list_skills()),
    }))
finally:
    skill.close()
    memory_store.close()
'''
    authored = _run("-c", author_script, str(memory_db), str(skill_config), cwd=repo)
    assert authored["search_count"] == 1
    assert authored["search_nonce"] == _NONCE
    assert authored["queue_state"] == "pending"
    assert authored["catalog_count"] == 0
    assert _NONCE in authored["markdown"]
    assert authored["proposal_tools"] == ["file.write", "file.read"]
    assert authored["evidence_refs"] == [
        "run_id:memory-author-run",
        "trace_id:memory-author-trace",
    ]
    proposal_id = str(authored["proposal"]["proposal_id"])

    cli = ("-m", "openminion.modules.skill.cli", "--config", str(skill_config))
    reviewed = _run(
        *cli,
        "proposal-review",
        proposal_id,
        "--criterion",
        "fit:accepted:bounded remembered procedure",
        cwd=repo,
    )
    assert reviewed["review"]["status"] == "accepted"
    verification = tmp_path / "verification.txt"
    verification.write_text(
        "passed: memory reconstruction contract\n", encoding="utf-8"
    )
    verified = _run(
        *cli,
        "proposal-verify",
        proposal_id,
        "--check",
        "memory-reconstruction-contract",
        "--evidence-ref",
        str(verification),
        cwd=repo,
    )
    assert verified["verification_evidence"]["result"] == "passed"
    applied = _run(*cli, "proposal-apply", proposal_id, cwd=repo)
    addition = applied["addition"]

    reuse_script = r"""
import json
import re
import sys
from pathlib import Path
from openminion.modules.skill.runtime.skill import Skill

skill = Skill(sys.argv[1])
try:
    skill_id = "memory_reconstruction_proof"
    package = skill.get_skill(skill_id)
    snippet, _ = skill.render_snippet(
        skill_id=skill_id,
        version_hash=package.version_hash,
        purpose="act",
        max_tokens=900,
    )
    nonce = re.search(r"memory-only-proof-[0-9]+", snippet).group(0)
    output = Path(sys.argv[2]) / "memory-reconstruction-proof.json"
    output.write_text(json.dumps({"marker": nonce}) + "\n", encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["marker"] == nonce
    run_id = skill.log_run(
        session_id="memory-reuse-session",
        agent_id="memory-skill-agent",
        skill_id=skill_id,
        version_hash=package.version_hash,
        used_for="act",
        outcome="success",
        evidence_refs=[str(output)],
    )
    print(json.dumps({
        "skill_id": skill_id,
        "version_hash": package.version_hash,
        "nonce": nonce,
        "tools": package.tools,
        "artifact": str(output),
        "run_id": run_id,
    }))
finally:
    skill.close()
"""
    reused = _run("-c", reuse_script, str(skill_config), str(tmp_path), cwd=repo)
    assert reused["skill_id"] == "memory_reconstruction_proof"
    assert reused["version_hash"] == addition["version_hash"]
    assert reused["nonce"] == _NONCE
    assert reused["tools"] == ["file.write", "file.read"]

    artifact = Path(str(reused["artifact"]))
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"marker": _NONCE}
    connection = sqlite3.connect(skill_db)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM skill_runs WHERE skill_id = ? AND outcome = 'success'",
            ("memory_reconstruction_proof",),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 1
