from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
FRAMEWORK_ROOT = Path(
    os.getenv("OPENMINION_SLOR_FRAMEWORK_ROOT", str(ROOT.parent))
).expanduser()
ARTIFACT_ROOT = Path(
    os.getenv(
        "OPENMINION_SLOR_ARTIFACT_ROOT",
        str(FRAMEWORK_ROOT / "workspace-tmp" / "skill-learning-operational-reuse"),
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

sys.path.insert(0, str(ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

if __name__ == "__main__":
    isolate_runtime_roots(prefix="openminion-slor-focus-")

from tests.e2e.cli.focus.harness import FocusProbe  # noqa: E402
from tests.e2e.cli.focus.harness.scenarios import FocusScenario  # noqa: E402


def _module_cli(
    data_root: Path,
    *args: str,
) -> dict[str, object]:
    env = os.environ.copy()
    env.update(
        {
            "OPENMINION_HOME": str(ARTIFACT_ROOT / "home"),
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


def _skill_runs(
    data_root: Path,
    skill_id: str,
    version_hash: str | None = None,
) -> list[dict[str, object]]:
    database = data_root / "skill" / "skills.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        if version_hash is None:
            rows = connection.execute(
                "SELECT * FROM skill_runs WHERE skill_id = ? ORDER BY created_at",
                (skill_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM skill_runs WHERE skill_id = ? AND version_hash = ? "
                "ORDER BY created_at",
                (skill_id, version_hash),
            ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def main() -> int:
    if os.getenv("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1":
        raise RuntimeError("OPENMINION_LIVE_CLI_FOCUS_E2E=1 is required")
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"MiniMax config not found: {CONFIG_PATH}")
    if not PYTHON_BIN.exists():
        raise RuntimeError(f"Python executable not found: {PYTHON_BIN}")

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    data_root = ARTIFACT_ROOT / "data"
    workspace = ARTIFACT_ROOT / "workspace"
    data_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    author_probe = FocusProbe(
        python_bin=PYTHON_BIN,
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=CONFIG_PATH,
        agent_id="minimax-m2-7",
        workdir=workspace,
        session_id="slor-focus-author",
        include_project_context=False,
    )
    author_scenario = FocusScenario(
        scenario_id="author",
        prompt=(
            "Call skill.propose exactly once to stage a reusable skill named "
            "slor-focus-reuse-proof. The skill_markdown argument must have this "
            "layout: YAML frontmatter containing name and description, closing ---, "
            "then # SLOR Focus Reuse Proof, then ## Procedure, then the instructions. "
            "Do not use a frontmatter content key or a version line. The procedure "
            "must tell a future run to write slor-focus-reuse-proof.json containing "
            "the JSON object {\"marker\": \"proof-8472\"}, then read the file "
            "back to verify it. Do not ingest or apply the skill. Finish by reporting "
            "the proposal ID."
        ),
        expected_markers=("proposal",),
        timeout=480,
        requires_approval=True,
        approval_reply="yes",
        max_auto_approvals=3,
    )
    with author_probe.session(rows=48, cols=160) as session:
        author_probe.wait_ready(session)
        author_transcript = author_probe.run_turn(session, author_scenario)
    author_path = ARTIFACT_ROOT / "author-focus.txt"
    author_path.write_text(author_transcript, encoding="utf-8")

    pending = _module_cli(data_root, "proposal-list", "--queue-state", "pending")
    proposals = list(pending.get("proposals") or [])
    proposal = next(
        row
        for row in proposals
        if row["proposal"]["proposed_skill_definition"]["name"]
        == "slor_focus_reuse_proof"
    )
    proposal_id = str(proposal["proposal_id"])
    proposal_evidence = proposal["proposal"]["evidence_refs"]
    assert any(str(ref).startswith("run_id:") for ref in proposal_evidence)
    assert any(str(ref).startswith("trace_id:") for ref in proposal_evidence)

    reviewed = _module_cli(
        data_root,
        "proposal-review",
        proposal_id,
        "--criterion",
        "fit:accepted:bounded reusable artifact procedure",
    )
    assert reviewed["review"]["status"] == "accepted"
    verification_dir = ARTIFACT_ROOT / "verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    verification_output = verification_dir / "slor-focus-reuse-proof.json"
    verification = subprocess.run(
        [
            str(PYTHON_BIN),
            "-c",
            (
                "import json, pathlib, sys; "
                "path = pathlib.Path(sys.argv[1]); "
                "path.write_text(json.dumps({'marker': 'proof-8472'}) + "
                "'\\n', encoding='utf-8'); "
                "assert json.loads(path.read_text(encoding='utf-8')) == "
                "{'marker': 'proof-8472'}; "
                "print('passed: deterministic artifact contract')"
            ),
            str(verification_output),
        ],
        cwd=verification_dir,
        text=True,
        capture_output=True,
        check=True,
    )
    verification_path = ARTIFACT_ROOT / "pre-admission-verification.txt"
    verification_path.write_text(verification.stdout, encoding="utf-8")
    verified = _module_cli(
        data_root,
        "proposal-verify",
        proposal_id,
        "--check",
        "deterministic-artifact-contract",
        "--evidence-ref",
        str(verification_path),
    )
    assert verified["verification_evidence"]["result"] == "passed"
    applied = _module_cli(data_root, "proposal-apply", proposal_id)
    addition = applied["addition"]
    skill_id = str(addition["added_skill_id"])
    version_hash = str(addition["version_hash"])
    runs_before = _skill_runs(data_root, skill_id, version_hash)

    reuse_probe = FocusProbe(
        python_bin=PYTHON_BIN,
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=CONFIG_PATH,
        agent_id="minimax-m2-7",
        workdir=workspace,
        session_id="slor-focus-reuse",
        include_project_context=False,
    )
    reuse_scenario = FocusScenario(
        scenario_id="reuse",
        prompt=(
            "Use the exact named skill slor_focus_reuse_proof. Follow its procedure "
            "now using file tools, including its read-back verification. Copy the "
            "marker exactly from the loaded skill instead of deriving it from the "
            "skill name. Finish with "
            "the exact text SLOR FOCUS REUSE OK."
        ),
        expected_markers=("SLOR FOCUS REUSE OK",),
        timeout=480,
        requires_approval=True,
        approval_reply="yes",
        max_auto_approvals=5,
    )
    output_path = workspace / "slor-focus-reuse-proof.json"
    assert not output_path.exists()
    with reuse_probe.session(rows=48, cols=160) as session:
        reuse_probe.wait_ready(session)
        reuse_transcript = reuse_probe.run_turn(session, reuse_scenario)
    reuse_path = ARTIFACT_ROOT / "reuse-focus.txt"
    reuse_path.write_text(reuse_transcript, encoding="utf-8")

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "marker": "proof-8472"
    }
    shown = _module_cli(data_root, "show", skill_id)
    assert shown["skill"]["version_hash"] == version_hash
    runs = _skill_runs(data_root, skill_id, version_hash)
    assert len(runs) == len(runs_before) + 1
    assert str(runs[-1]["session_id"]).startswith("slor-focus-reuse::conv:")
    assert runs[-1]["outcome"] == "success"

    (ARTIFACT_ROOT / "summary.json").write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "skill_id": skill_id,
                "version_hash": version_hash,
                "artifact": str(output_path),
                "run_id": runs[-1]["run_id"],
                "author_transcript": str(author_path),
                "reuse_transcript": str(reuse_path),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"SLOR Focus MiniMax passed: {ARTIFACT_ROOT / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
