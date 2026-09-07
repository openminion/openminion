from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
FRAMEWORK_ROOT = Path(
    os.getenv("OPENMINION_SMRR_FRAMEWORK_ROOT", str(ROOT.parent))
).expanduser()
ARTIFACT_BASE = Path(
    os.getenv(
        "OPENMINION_SMRR_ARTIFACT_ROOT",
        str(
            FRAMEWORK_ROOT
            / "workspace-tmp"
            / "skill-memory-reconstruction-reuse-20260906"
            / "focus"
        ),
    )
).expanduser()
CONFIG_PATH = Path(
    os.getenv(
        "OPENMINION_CLI_FOCUS_E2E_CONFIG",
        str(FRAMEWORK_ROOT / "test-configs" / "per-agent-minimax-official.json"),
    )
).expanduser()
PYTHON_BIN = Path(
    os.getenv("OPENMINION_PYTHON", str(ROOT / ".venv" / "bin" / "python3.11"))
).expanduser()
NONCE = "focus-memory-only-proof-5831"
SKILL_NAME = "smrr-focus-memory-reuse"
SKILL_ID = "smrr_focus_memory_reuse"

sys.path.insert(0, str(ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

if __name__ == "__main__":
    isolate_runtime_roots(prefix="openminion-smrr-focus-")

from openminion.modules.brain.adapters.memory import MemctlAdapter  # noqa: E402
from openminion.modules.memory.service import MemoryService  # noqa: E402
from openminion.modules.memory.storage.sqlite.store import (  # noqa: E402
    SQLiteMemoryStore,
)
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario  # noqa: E402


def _json_rows(database: Path, table: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(f"SELECT rowid AS _rowid, * FROM {table}")
        ]
    finally:
        connection.close()


def _module_cli(data_root: Path, *args: str) -> dict[str, object]:
    env = os.environ.copy()
    env.update(
        {
            "OPENMINION_HOME": str(data_root.parent / "operator-home"),
            "OPENMINION_DATA_ROOT": str(data_root),
            "OPENMINION_GENERATED_ROOT": str(data_root / "runtime"),
            "PYTHONPATH": "src",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        [
            str(PYTHON_BIN),
            "-m",
            "openminion.modules.skill.cli",
            "--config",
            str(CONFIG_PATH),
            *args,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _seed_promoted_procedure(data_root: Path) -> str:
    memory_path = data_root / "memory" / "memory.db"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteMemoryStore(memory_path)
    service = MemoryService(store=store)
    adapter = MemctlAdapter(service, agent_id="minimax-m2-7")
    try:
        candidate_id = adapter.stage_candidate(
            scope="agent:minimax-m2-7",
            record_type="procedure",
            title="Reconstruct the Focus memory proof report",
            content={
                "steps": [
                    "Write smrr-focus-memory-proof.json as a JSON object whose marker is "
                    + NONCE,
                    "Read the file back and confirm the marker matches exactly",
                ],
                "validation": ["The marker must equal the remembered marker"],
                "rollback_hint": "Remove the generated file if validation fails",
                "proof_nonce": NONCE,
                "tools": ["file.write", "file.read"],
            },
            tags=["memory-reconstruction", "focus-proof"],
            evidence_refs=["run_id:smrr-focus-setup"],
            confidence=0.95,
            meta={"source_success_path": True},
        )
        service.candidate_update(candidate_id, {"status": "approved"})
        record = service.promote_candidate(candidate_id, "agent:minimax-m2-7")
        return record.id
    finally:
        store.close()


def _tool_requests(
    data_root: Path, *, trace_id: str | None = None
) -> list[dict[str, object]]:
    database = data_root / "telemetry" / "telemetry.db"
    rows = _json_rows(database, "events")
    requests: list[dict[str, object]] = []
    for row in rows:
        if row.get("event_type") != "tool.call.requested":
            continue
        data = json.loads(str(row.get("data") or "{}"))
        if trace_id is not None and str(data.get("turn_scope_id") or "") != trace_id:
            continue
        requests.append(
            {
                "rowid": row["_rowid"],
                "tool": str(data.get("canonical_name") or ""),
                "call_id": str(data.get("call_id") or ""),
                "arguments": data.get("sanitized_normalized_arguments") or {},
            }
        )
    return requests


def _selected_skill_trace(data_root: Path) -> str:
    database = data_root / "telemetry" / "telemetry.db"
    for row in reversed(_json_rows(database, "events")):
        if row.get("event_type") != "skill.selected":
            continue
        data = json.loads(str(row.get("data") or "{}"))
        if data.get("primary_skill_id") == SKILL_ID:
            return str(data["trace_id"])
    raise AssertionError(f"no selection event found for {SKILL_ID}")


def _author_request_traces(data_root: Path, session_id: str) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []
    for path in (data_root / "traces").rglob("*-http.json"):
        if session_id not in str(path):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        request = payload.get("json") or payload.get("json_body")
        if not isinstance(request, dict):
            continue
        request["_path"] = str(path)
        requests.append(request)
    return sorted(requests, key=lambda item: str(item["_path"]))


def _first_nonce_tool_result(requests: list[dict[str, object]]) -> str:
    for request_index, request in enumerate(requests):
        messages = list(request.get("messages") or [])
        for message_index, message in enumerate(messages):
            if NONCE not in json.dumps(message, sort_keys=True):
                continue
            assert all(
                NONCE not in json.dumps(previous, sort_keys=True)
                for previous_request in requests[:request_index]
                for previous in list(previous_request.get("messages") or [])
            )
            assert all(
                NONCE not in json.dumps(previous, sort_keys=True)
                for previous in messages[:message_index]
            )
            assert message["role"] == "tool"
            tool_call_id = str(message.get("tool_call_id") or "")
            assert tool_call_id
            assistant = messages[message_index - 1]
            assert assistant["role"] == "assistant"
            calls = list(assistant.get("tool_calls") or [])
            matching = [
                call for call in calls if str(call.get("id") or "") == tool_call_id
            ]
            assert len(matching) == 1
            function_name = str((matching[0].get("function") or {}).get("name") or "")
            assert function_name in {"memory.search", "memory_search"}
            return str(request["_path"])
    raise AssertionError("nonce did not appear in any provider request")


def _skill_runs(data_root: Path, skill_id: str, version_hash: str) -> list[dict]:
    database = data_root / "skill" / "skills.db"
    return [
        row
        for row in _json_rows(database, "skill_runs")
        if row["skill_id"] == skill_id and row["version_hash"] == version_hash
    ]


def main() -> int:
    if os.getenv("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1":
        raise RuntimeError("OPENMINION_LIVE_CLI_FOCUS_E2E=1 is required")
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"MiniMax config not found: {CONFIG_PATH}")
    if not PYTHON_BIN.exists():
        raise RuntimeError(f"Python executable not found: {PYTHON_BIN}")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_root = ARTIFACT_BASE / run_stamp
    data_root = artifact_root / "data"
    workspace = artifact_root / "workspace"
    data_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    os.environ["OPENMINION_MEMORY_CAPSULE_STRATEGY"] = "off"
    os.environ["OPENMINION_TRACE_REQUESTS"] = "1"
    record_id = _seed_promoted_procedure(data_root)

    author_session_id = f"smrr-focus-author-{run_stamp.lower()}"
    author_probe = FocusProbe(
        python_bin=PYTHON_BIN,
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=CONFIG_PATH,
        agent_id="minimax-m2-7",
        workdir=workspace,
        session_id=author_session_id,
        include_project_context=False,
    )
    author_scenario = FocusScenario(
        scenario_id="memory-author",
        prompt=(
            "Search your saved procedure memory for the procedure titled "
            "'Reconstruct the Focus memory proof report'. Use memory.search "
            "explicitly. Then call skill.propose exactly once to stage a complete "
            f"portable skill named {SKILL_NAME} that preserves and follows the "
            "remembered procedure. Copy the retrieved content.tools list exactly "
            "into YAML frontmatter tools, without omitting or relocating it. Put the "
            "retrieved procedure itself in the skill; "
            "do not make the skill search memory or propose another skill. Do not "
            "ingest or apply it. Finish by reporting the proposal ID."
        ),
        expected_markers=("proposal",),
        timeout=600,
        requires_approval=True,
        approval_reply="yes",
        max_auto_approvals=4,
    )
    assert record_id not in author_scenario.prompt
    assert NONCE not in author_scenario.prompt
    assert "file.write" not in author_scenario.prompt
    assert "file.read" not in author_scenario.prompt
    with author_probe.session(rows=48, cols=160) as session:
        author_probe.wait_ready(session)
        author_transcript = author_probe.run_turn(session, author_scenario)
    author_path = artifact_root / "author-focus.txt"
    author_path.write_text(author_transcript, encoding="utf-8")

    pending = _module_cli(data_root, "proposal-list", "--queue-state", "pending")
    proposals = list(pending.get("proposals") or [])
    proposal = next(
        row
        for row in proposals
        if row["proposal"]["proposed_skill_definition"]["name"] == SKILL_ID
    )
    proposal_id = str(proposal["proposal_id"])
    proposal_markdown = str(proposal["proposal"]["skill_markdown"])
    assert NONCE in proposal_markdown
    proposed_tools = (
        proposal["proposal"]["proposed_skill_definition"].get("tools") or []
    )
    assert proposed_tools == ["file.write", "file.read"]
    assert _module_cli(data_root, "list")["skills"] == []

    tool_requests = _tool_requests(data_root)
    ordered_tools = [row["tool"] for row in tool_requests]
    search_index = ordered_tools.index("memory.search")
    propose_index = ordered_tools.index("skill.propose")
    assert search_index < propose_index

    provider_requests = _author_request_traces(data_root, author_session_id)
    assert provider_requests
    first_nonce_request = _first_nonce_tool_result(provider_requests)

    reviewed = _module_cli(
        data_root,
        "proposal-review",
        proposal_id,
        "--criterion",
        "fit:accepted:memory-grounded portable procedure",
    )
    assert reviewed["review"]["status"] == "accepted"
    verification_path = artifact_root / "pre-admission-verification.txt"
    verification_path.write_text(
        "passed: memory-only nonce and portable procedure present\n",
        encoding="utf-8",
    )
    verified = _module_cli(
        data_root,
        "proposal-verify",
        proposal_id,
        "--check",
        "memory-reconstruction-contract",
        "--evidence-ref",
        str(verification_path),
    )
    assert verified["verification_evidence"]["result"] == "passed"
    applied = _module_cli(data_root, "proposal-apply", proposal_id)
    addition = applied["addition"]
    version_hash = str(addition["version_hash"])
    runs_before = _skill_runs(data_root, SKILL_ID, version_hash)

    reuse_session_id = f"smrr-focus-reuse-{run_stamp.lower()}"
    reuse_probe = FocusProbe(
        python_bin=PYTHON_BIN,
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=CONFIG_PATH,
        agent_id="minimax-m2-7",
        workdir=workspace,
        session_id=reuse_session_id,
        include_project_context=False,
    )
    reuse_scenario = FocusScenario(
        scenario_id="memory-reuse",
        prompt=(
            f"Use the exact named skill {SKILL_ID}. Follow its procedure now, "
            "including file creation and read-back validation. Copy the marker "
            "exactly from the loaded skill. Finish with the exact text "
            "SMRR FOCUS REUSE OK."
        ),
        expected_markers=("SMRR FOCUS REUSE OK",),
        timeout=600,
        requires_approval=True,
        approval_reply="yes",
        max_auto_approvals=6,
    )
    output_path = workspace / "smrr-focus-memory-proof.json"
    assert not output_path.exists()
    with reuse_probe.session(rows=48, cols=160) as session:
        reuse_probe.wait_ready(session)
        reuse_transcript = reuse_probe.run_turn(session, reuse_scenario)
    reuse_path = artifact_root / "reuse-focus.txt"
    reuse_path.write_text(reuse_transcript, encoding="utf-8")

    output_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert output_payload["marker"] == NONCE
    runs = _skill_runs(data_root, SKILL_ID, version_hash)
    assert len(runs) == len(runs_before) + 1
    assert runs[-1]["outcome"] == "success"
    reuse_trace_id = _selected_skill_trace(data_root)
    reuse_tools = [
        row["tool"] for row in _tool_requests(data_root, trace_id=reuse_trace_id)
    ]
    assert reuse_tools.index("file.write") < reuse_tools.index("file.read")

    summary = {
        "record_id": record_id,
        "proposal_id": proposal_id,
        "skill_id": SKILL_ID,
        "version_hash": version_hash,
        "artifact": str(output_path),
        "run_id": runs[-1]["run_id"],
        "tool_order": ordered_tools,
        "reuse_tool_order": reuse_tools,
        "first_nonce_request": first_nonce_request,
        "author_transcript": str(author_path),
        "reuse_transcript": str(reuse_path),
    }
    summary_path = artifact_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"SMRR Focus MiniMax passed: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
