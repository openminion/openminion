from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from openminion.modules.artifact.config import (
    ArtifactCtlConfig,
    BlobStoreConfig,
    IndexConfig,
)
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.tools.security.schemas import SecurityAuditReport
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.probe import focus_session_id

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(900)]

EXPECTED_TOOLS = {
    "file.list_dir",
    "file.read",
    "file.read_range",
    "file.find",
    "code.grep",
    "code.repo_map",
    "code.repo_index",
    "code.symbol_find",
    "git.status",
    "git.diff",
    "git.log",
    "git.show",
    "git.blame",
    "security.scan_code",
    "security.scan_dependencies",
    "security.scan_artifact",
    "security.scan_secrets",
    "security.publish_report",
}


def _run(command: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local E2E argv
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout.strip()


def _prepare_target(target: Path) -> str:
    target.mkdir()
    (target / "app.py").write_text(
        "def evaluate(user_input):\n    return eval(user_input)\n",
        encoding="utf-8",
    )
    (target / "requirements.txt").write_text("Django==2.2.0\n", encoding="utf-8")
    (target / "main.tf").write_text(
        'resource "aws_security_group_rule" "open" {\n'
        '  type = "ingress"\n  cidr_blocks = ["0.0.0.0/0"]\n}\n',
        encoding="utf-8",
    )
    (target / "fixture.env").write_text(
        "password=audit-fixture-password\n", encoding="utf-8"
    )
    (target / "semgrep.yml").write_text(
        "rules:\n"
        "  - id: openminion.audit.dangerous-eval\n"
        "    message: Avoid evaluating untrusted input.\n"
        "    severity: ERROR\n"
        "    languages: [python]\n"
        "    pattern: eval(...)\n",
        encoding="utf-8",
    )
    git = ("git", "-C", str(target))
    _run((*git, "init", "-q", "-b", "main"), cwd=target, env=os.environ.copy())
    _run(
        (*git, "config", "user.email", "security-e2e@example.invalid"),
        cwd=target,
        env=os.environ.copy(),
    )
    _run(
        (*git, "config", "user.name", "Security E2E"),
        cwd=target,
        env=os.environ.copy(),
    )
    _run(
        (*git, "config", "commit.gpgsign", "false"),
        cwd=target,
        env=os.environ.copy(),
    )
    _run((*git, "add", "."), cwd=target, env=os.environ.copy())
    _run(
        (*git, "commit", "-q", "-m", "vulnerable fixture"),
        cwd=target,
        env=os.environ.copy(),
    )
    return _run((*git, "rev-parse", "HEAD"), cwd=target, env=os.environ.copy())


def _prepare_config(source: Path, destination: Path, target: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    profile = dict(payload["agents"]["minimax-m2-7"])
    profile.update(
        name="security-researcher-readonly",
        role="readonly security researcher",
        skill="security-researcher-readonly",
    )
    payload["agents"]["security-researcher-readonly"] = profile
    payload["identity"] = {"db_path": "identity/identity.db"}
    runtime_env = payload.setdefault("runtime", {}).setdefault("env", {})
    runtime_env["OPENMINION_SECURITY_SEMGREP_CONFIG"] = str(target / "semgrep.yml")
    runtime_env["OPENMINION_SECURITY_ALLOWED_ROOTS"] = str(target)
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _telemetry_events(data_root: Path, session_id: str) -> list[tuple[str, dict]]:
    with sqlite3.connect(data_root / "telemetry" / "telemetry.db") as connection:
        rows = connection.execute(
            "SELECT event_type, data FROM events "
            "WHERE session_id = ? OR session_id LIKE ? ORDER BY id",
            (session_id, f"{session_id}::%"),
        ).fetchall()
    return [(str(event_type), json.loads(str(data))) for event_type, data in rows]


def _install_profile(
    *,
    python_bin: Path,
    openminion_root: Path,
    env: dict[str, str],
) -> None:
    skill_path = (
        openminion_root
        / "examples"
        / "skills"
        / "security-researcher-readonly"
        / "SKILL.md"
    )
    ingested = json.loads(
        _run(
            (
                str(python_bin),
                "-m",
                "openminion",
                "skill",
                "ingest",
                "--file",
                str(skill_path),
                "--name",
                "security-researcher-readonly",
                "--scope",
                "agent",
                "--agent-id",
                "security-researcher-readonly",
                "--trust",
                "trusted_local",
            ),
            cwd=openminion_root,
            env=env,
        )
    )
    _run(
        (
            str(python_bin),
            "-m",
            "openminion",
            "skill",
            "admit",
            "--skill-id",
            "security-researcher-readonly",
            "--version-hash",
            ingested["version_hash"],
            "--expected-active-version-hash",
            "none",
            "--target-status",
            "verified",
            "--reason",
            "Approved local readonly security procedure",
        ),
        cwd=openminion_root,
        env=env,
    )
    _run(
        (
            str(python_bin),
            "-m",
            "openminion",
            "identity",
            "upsert",
            str(
                openminion_root
                / "examples"
                / "identity"
                / "security-researcher-readonly.yaml"
            ),
        ),
        cwd=openminion_root,
        env=env,
    )


def test_live_security_researcher_publishes_candidate_report(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    framework_root: Path,
    minimax_config_path: Path,
) -> None:
    require_live_focus()
    root = artifact_root(tmp_path)
    data_root = root / "data" / "security-researcher"
    target = root / "target"
    revision = _prepare_target(target)
    session_id = focus_session_id(data_root=data_root, node_name="security-researcher")
    config_path = (
        data_root.parent / "home-roots" / session_id / ".openminion" / "agents.json"
    )
    _prepare_config(minimax_config_path, config_path, target)
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root,
        data_root=data_root,
        config_path=config_path,
        agent_id="security-researcher-readonly",
        workdir=target,
        session_id=session_id,
        include_project_context=False,
        allow_unsandboxed_exec=False,
    )
    env = {**os.environ, **probe.environment()}
    _install_profile(
        python_bin=python_bin,
        openminion_root=openminion_root,
        env=env,
    )
    scenario = FocusScenario(
        scenario_id="security-researcher-candidate-report",
        prompt=(
            "Audit this approved clean local repository at revision "
            f"{revision}. Run exactly the code, dependencies, iac, and secrets "
            "security checks. For every scan call, use the exact arguments "
            f"target={target}, expected_target_revision={revision}, and "
            "include_evidence_artifact=true. "
            "Then publish one unreviewed candidate report from the canonical "
            "terminal evidence. Do not modify "
            "the target. Include exactly one manual candidate finding and no "
            "scanner-based findings. Before writing it, call file.read_range "
            "for app.py lines 1 through 2. The finding must identify the eval "
            "call on line 2, use location "
            '{"path":"app.py","line":2}, and include a short read-only '
            "evidence summary. End with the report execution status."
        ),
        expected_markers=("completed|partial|blocked",),
        timeout=900,
    )

    with probe.session(rows=52, cols=160) as session:
        probe.wait_ready(session)
        probe.run_slash(session, "/readonly on", marker="read-only mode: ON")
        probe.run_slash(
            session,
            "/tools activate security_readonly approved=yes",
            marker="Activated: security_readonly",
        )
        transcript = probe.run_turn(session, scenario)
        write_transcript(root, scenario.scenario_id, transcript)

    assert (
        _run(
            (
                "git",
                "-C",
                str(target),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
            cwd=target,
            env=os.environ.copy(),
        )
        == ""
    )
    artifact_root_path = data_root / "artifact"
    artifact_config = ArtifactCtlConfig(
        blob_store=BlobStoreConfig(root_dir=str(artifact_root_path)),
        index=IndexConfig(sqlite_path=str(artifact_root_path / "index.db")),
    )
    with ArtifactCtl(artifact_config) as artifactctl:
        reports = []
        for item in artifactctl.list_recent(limit=20):
            try:
                reports.append(
                    SecurityAuditReport.model_validate_json(
                        artifactctl.read_bytes(item.sha256)
                    )
                )
            except ValueError:
                continue
    assert len(reports) == 1
    report = reports[0]
    assert report.scope.target_revision == revision
    assert report.scope.requested_checks == ["code", "dependencies", "iac", "secrets"]
    assert report.review_status == "unreviewed"
    assert report.execution_status in {"completed", "partial", "blocked"}
    assert len(report.checks) == 4
    assert len(report.evidence_refs) == 4
    assert len(set(report.evidence_refs)) == 4
    assert all(ref.startswith("artifact://sha256/") for ref in report.evidence_refs)
    candidates = [item for item in report.findings if item.disposition == "candidate"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.locations[0].path == "app.py"
    assert candidate.locations[0].line == 2
    grounded_text = " ".join(
        (
            candidate.title,
            candidate.description,
            candidate.validation,
            candidate.evidence_summary,
        )
    ).lower()
    assert "eval" in grounded_text

    telemetry_events = _telemetry_events(data_root, session_id)
    allowed_tool_sets = [
        set(payload["adaptive.allowed_tools"])
        for event_type, payload in telemetry_events
        if event_type == "brain.execution_status"
        and payload.get("adaptive.allowed_tools")
    ]
    assert allowed_tool_sets
    assert all(tool_set == EXPECTED_TOOLS for tool_set in allowed_tool_sets)
    tool_calls = {
        tool_name
        for event_type, payload in telemetry_events
        if event_type == "brain.execution_status"
        for tool_name in payload.get("adaptive.tool_calls", [])
    }
    assert "file.read_range" in tool_calls

    persisted_events = [payload for _event_type, payload in telemetry_events]
    session_store = SQLiteSessionStore(data_root / "state" / "brain" / "sessions.db")
    try:
        persisted_events.extend(
            session_store.get_tool_transcript(session_id).get("events", [])
        )
    finally:
        session_store.close()
    persisted_text = json.dumps(persisted_events, sort_keys=True)
    for prohibited in (
        str(target),
        "return eval(user_input)",
        "Avoid evaluating untrusted input.",
        "audit-fixture-password",
        "adaptive.finalization_status",
    ):
        assert prohibited not in persisted_text
    terminal = next(
        payload
        for event_type, payload in telemetry_events
        if event_type == "brain.execution_status"
        and payload.get("adaptive.termination_reason")
    )
    assert terminal["report_published"] is True
    assert terminal["result_status"] == report.execution_status
