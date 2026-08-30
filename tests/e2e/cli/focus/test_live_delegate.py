from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import time

import pytest

from openminion.api.handoff import SubagentRunContext
from openminion.modules.brain.schemas import WorkingState
from openminion.modules.memory.adapters import OpenMinionDelegationMemoryGrantResolver
from openminion.modules.policy.models import PolicyConfig, PolicyGrantInput
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.session.storage import SQLiteSessionStore
from sophiagraph import MemoryRecord
from sophiagraph.access import (
    AccessConstraint,
    AuthorizedSophiaGraphGateway,
    MemoryAccessContext,
    MemoryAccessRequest,
)
from sophiagraph.models import MemoryNamespace
from sophiagraph.storage import SophiaGraphSqliteStore
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(360)]

_TASK_ID_RE = re.compile(r"(?m)^\s*task\s+(\S+)\s*$")


def _sgdm_target(namespace: MemoryNamespace) -> dict:
    return {
        "resource": "sophiagraph",
        "delegated_memory": {
            "version": 1,
            "audience": "sophiagraph",
            "delegator_agent_id": "parent",
            "subject_agent_id": "child",
            "parent_run_id": "parent-run",
            "child_run_id": "child-run",
            "trace_parent_id": "trace-live",
            "namespaces": [namespace.as_dict()],
            "workspace_ids": ["workspace-live"],
            "operations": ["read"],
            "record_types": ["fact"],
            "max_results": 1,
            "max_context_tokens": 64,
            "max_depth": 1,
            "can_reshare": False,
        },
    }


def _resolve_live_delegated_record(root: Path) -> tuple[MemoryRecord, dict]:
    allowed_namespace = MemoryNamespace(
        agent_id="child", project_id="sgdm-live", graph_id="main"
    )
    sibling_namespace = MemoryNamespace(
        agent_id="sibling", project_id="sgdm-live", graph_id="main"
    )
    store = SophiaGraphSqliteStore(root / "sophiagraph.sqlite3")
    for record_id, namespace, text in (
        ("sgdm-allowed", allowed_namespace, "Project Atlas uses cobalt blue."),
        ("sgdm-hidden", sibling_namespace, "Sibling secret must stay private."),
    ):
        store.put_record(
            MemoryRecord(
                id=record_id,
                scope=f"agent:{namespace.agent_id}",
                type="fact",
                content={"text": text},
                created_at="2026-08-06T00:00:00+00:00",
                updated_at="2026-08-06T00:00:00+00:00",
                namespace=namespace,
            )
        )
    policy = PolicyCtl.with_sqlite(
        root / "policy.sqlite3", config=PolicyConfig(mode="enforce")
    )
    try:
        grant_id = policy.create_grant(
            PolicyGrantInput(
                effect="allow",
                subject_id="child",
                tool="memory",
                method="delegated_read",
                target_json=_sgdm_target(allowed_namespace),
                duration_type="until",
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            )
        )
        run_context = SubagentRunContext(
            context_id="context-live",
            parent_agent_id="parent",
            child_agent_id="child",
            parent_run_id="parent-run",
            child_run_id="child-run",
            trace_parent_id="trace-live",
            memory_posture="read_only_bounded",
            memory_grant_id=grant_id,
        )
        resolver = OpenMinionDelegationMemoryGrantResolver(
            policy,
            run_context,
            memory_scope_namespaces=(allowed_namespace,),
        )
        audit_events = []
        gateway = AuthorizedSophiaGraphGateway(
            store, resolver=resolver, audit_recorder=audit_events.append
        )
        context = MemoryAccessContext(
            principal_id="child-principal",
            audience="sophiagraph",
            subject_agent_id="child",
            parent_run_id="parent-run",
            child_run_id="child-run",
            trace_parent_id="trace-live",
            delegated=True,
            constraints=(
                AccessConstraint(
                    mode="allowlist",
                    namespaces=(allowed_namespace,),
                    workspace_ids=("workspace-live",),
                    operations=("read",),
                    record_types=("fact",),
                    max_results=1,
                    max_context_tokens=64,
                ),
            ),
        )
        request = MemoryAccessRequest(
            operation="read",
            grant_id=grant_id,
            namespaces=(allowed_namespace,),
            workspace_ids=("workspace-live",),
            record_types=("fact",),
            max_results=1,
            max_context_tokens=64,
        )
        allowed = gateway.get_record("sgdm-allowed", context=context, request=request)
        hidden = gateway.get_record("sgdm-hidden", context=context, request=request)
        assert allowed is not None
        assert hidden is None
        return allowed, {
            "grant_bound": True,
            "allowed_record_id": allowed.id,
            "sibling_denied": True,
            "denial_reason": audit_events[-1].details["reason"],
        }
    finally:
        policy.close()


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _create_disposable_repo(root: Path) -> Path:
    repo = root / "maer-parent"
    repo.mkdir(parents=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "maer-live@example.invalid")
    _run_git(repo, "config", "user.name", "MAER Live")
    (repo / "seed.txt").write_text("parent unchanged\n", encoding="utf-8")
    _run_git(repo, "add", "seed.txt")
    _run_git(repo, "commit", "-m", "seed")
    return repo


def _latest_working_state(probe: FocusProbe) -> WorkingState:
    store = SQLiteSessionStore(probe.data_root / "state" / "brain" / "sessions.db")
    try:
        brain_session_id = f"{probe.session_id}::conv:focus-{probe.session_id}"
        latest = store.get_latest_working_state(brain_session_id)
    finally:
        store.close()
    assert latest is not None
    return WorkingState.model_validate(latest["state_inline"])


def _latest_public_child_artifacts(probe: FocusProbe) -> list[dict]:
    state = _latest_working_state(probe)
    assert state.last_result is not None
    subtask_results = list(state.last_result.outputs.get("subtask_results", []))
    artifacts = [
        dict(item["child_artifact"])
        for item in subtask_results
        if isinstance(item, dict) and isinstance(item.get("child_artifact"), dict)
    ]
    assert artifacts, "orchestration result did not expose child artifacts"
    return artifacts


def _wait_for_a2a_delegate_success(
    root, scenario: str, *, timeout: float = 60.0
) -> None:
    audit_dir = root / "data" / scenario / "a2a" / "audit"
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        db_paths = sorted(audit_dir.glob("*.db"))
        for db_path in db_paths:
            try:
                with sqlite3.connect(db_path) as conn:
                    count = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM audit_records
                        WHERE method = 'delegate'
                          AND status = 'SUCCESS'
                        """
                    ).fetchone()[0]
            except sqlite3.Error as exc:
                last_error = str(exc)
                continue
            if count:
                return
        time.sleep(0.1)
    raise AssertionError(
        "timed out waiting for delegated child SUCCESS audit row"
        f" under {audit_dir}; last_error={last_error}"
    )


def _wait_for_a2a_job_state(
    probe: FocusProbe,
    task_id: str,
    expected_state: str,
    *,
    timeout: float = 60.0,
) -> None:
    database = probe.data_root / "a2a" / "state.db"
    deadline = time.monotonic() + timeout
    last_state = ""
    while time.monotonic() < deadline:
        try:
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT state FROM jobs WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            last_state = str(row[0])
            if last_state == expected_state:
                return
        time.sleep(0.1)
    raise AssertionError(
        f"task {task_id} did not reach {expected_state}; last_state={last_state}"
    )


def _latest_delegated_child_answer(probe: FocusProbe) -> str:
    database = probe.data_root / "state" / "brain" / "sessions.db"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT content
            FROM turns
            WHERE role = 'assistant' AND session_id LIKE 'task-delegate::%'
            ORDER BY ts DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None, "delegated child result was not persisted"
    return str(row[0])


def _delegated_child_answer_count(probe: FocusProbe) -> int:
    database = probe.data_root / "state" / "brain" / "sessions.db"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM turns
            WHERE role = 'assistant' AND session_id LIKE 'task-delegate::%'
            """
        ).fetchone()
    return int(row[0]) if row is not None else 0


def test_live_focus_delegate_exact_target(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    """Prove Focus can delegate to a named configured MiniMax target."""
    require_live_focus()
    marker = "MAER exact target OK"
    target_agent = "minimax-m2-7-highspeed"
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(
            session,
            f"/delegate {target_agent} Reply exactly: {marker}",
            marker="Delegation:",
        )
        write_transcript(
            artifact_root(tmp_path),
            "maer-live-delegate-exact-target",
            transcript,
        )
    assert "Delegation:" in transcript
    assert target_agent in transcript


def test_live_focus_delegate_uses_bounded_sophiagraph_context(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    """Prove a live child receives only the authorized SQLite memory excerpt."""

    require_live_focus()
    root = artifact_root(tmp_path)
    record, evidence = _resolve_live_delegated_record(root)
    marker = "SGDM_LIVE_ALLOWED [record:sgdm-allowed]"
    prompt = (
        "/delegate minimax-m2-7-highspeed Use only this delegated memory: "
        f"{record.content['text']} Citation: [record:{record.id}]. "
        f"Reply exactly: {marker}"
    )
    with focus_probe.session(rows=50, cols=160) as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(session, prompt, marker="Delegation:")
        _wait_for_a2a_delegate_success(
            root,
            "test_live_focus_delegate_uses_bounded_sophiagraph_context",
            timeout=120,
        )
        child_answer = _latest_delegated_child_answer(focus_probe)
        transcript_path = write_transcript(
            root,
            "sgdm-live-bounded-project-recall",
            f"{transcript}\n\n--- durable child result ---\n{child_answer}\n",
        )

    assert marker in child_answer
    assert "sgdm-hidden" not in child_answer
    evidence.update(
        {
            "provider_tier": "live",
            "transport": "openminion-focus-delegate",
            "backend": "sophiagraph-sqlite",
            "transcript": transcript_path.name,
        }
    )
    (root / "sgdm-live-bounded-project-recall.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.timeout(660)
def test_live_focus_delegate_code_child_writes_in_scratch(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    """Exercise a live code-bearing child turn without touching the repo."""
    require_live_focus()
    marker = "MAER_CODE_CHILD_OK"
    target_agent = "minimax-m2-7-highspeed"
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / "maer-live-code-child"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    active_probe = focus_probe.for_workdir(
        scratch_dir,
        include_project_context=False,
    )
    with active_probe.session(rows=50, cols=160) as session:
        active_probe.wait_ready(session)
        transcript = active_probe.run_slash_turn(
            session,
            (
                f"/delegate {target_agent} In the current directory, create "
                "`maer_child_marker.txt` containing exactly "
                f"`{marker}`. Use file.write for the file, then read it back "
                f"and reply with `{marker}`."
            ),
            marker=None,
            timeout=600,
            requires_approval=True,
            max_auto_approvals=8,
            approval_reply="session",
        )
        write_transcript(root, "maer-live-code-child", transcript)
        _wait_for_a2a_delegate_success(
            root,
            "test_live_focus_delegate_code_child_writes_in_scratch",
        )

    marker_file = scratch_dir / "maer_child_marker.txt"
    assert marker_file.read_text(encoding="utf-8").strip() == marker


def test_live_focus_delegate_async_lifecycle(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    """Prove the live Focus surface preserves one async task handle."""
    require_live_focus()
    marker = "SDUC_ASYNC_RESULT_OK"
    target_agent = "minimax-m2-7-highspeed"
    root = artifact_root(tmp_path)
    with focus_probe.session(rows=50, cols=160) as session:
        focus_probe.wait_ready(session)
        started = focus_probe.run_slash(
            session,
            (f"/delegate async {target_agent} Reply with exactly `{marker}`."),
            marker="Delegation:",
        )
        task_match = _TASK_ID_RE.search(started)
        assert task_match is not None, started
        task_id = task_match.group(1)

        status = focus_probe.run_slash(
            session, f"/delegate status {task_id}", marker="Delegation:"
        )
        _wait_for_a2a_delegate_success(
            root,
            "test_live_focus_delegate_async_lifecycle",
            timeout=120,
        )
        result = focus_probe.run_slash_turn(
            session,
            f"/delegate result {task_id}",
            marker=marker,
        )
        write_transcript(
            root,
            "sduc-live-async-lifecycle",
            "\n\n--- status ---\n".join((started, status, result)),
        )

    for transcript in (status, result):
        assert task_id in transcript
        assert "Delegation:" in transcript
    assert "status    completed" in result
    assert "Delegation failed" not in result


def test_live_focus_cancel_stops_active_child_turn(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    """Prove Focus cancellation stops an active configured child turn."""
    require_live_focus()
    root = artifact_root(tmp_path)
    target_agent = "minimax-m2-7-highspeed"
    with focus_probe.session(rows=50, cols=160) as session:
        focus_probe.wait_ready(session)
        started = focus_probe.run_slash(
            session,
            (
                f"/delegate async {target_agent} Use web.search for three distinct "
                "current technology queries one after another, summarize the results, "
                "and then reply exactly CANCEL_CHILD_FINISHED."
            ),
            marker="Delegation:",
        )
        task_match = _TASK_ID_RE.search(started)
        assert task_match is not None, started
        task_id = task_match.group(1)
        _wait_for_a2a_job_state(focus_probe, task_id, "RUNNING")

        canceled = focus_probe.run_slash(
            session,
            f"/delegate cancel {task_id}",
            marker="status    cancelled",
        )
        _wait_for_a2a_job_state(focus_probe, task_id, "CANCELED")
        time.sleep(5)
        status = focus_probe.run_slash(
            session,
            f"/delegate status {task_id}",
            marker="status    cancelled",
        )
        write_transcript(
            root,
            "sduc-live-active-cancellation",
            f"{started}\n\n--- cancel ---\n{canceled}"
            f"\n\n--- final status ---\n{status}\n",
        )

    assert _delegated_child_answer_count(focus_probe) == 0
    assert "CANCEL_CHILD_FINISHED" not in status


@pytest.mark.timeout(660)
def test_live_focus_reassigns_failed_child_to_exact_available_agent(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    """Prove MiniMax can recover one failed child through exact reassignment."""
    require_live_focus()
    root = artifact_root(tmp_path)
    prompt = (
        "Use the decompose tool exactly once with exactly two sequential subtasks. "
        "Do not bypass the deliberately missing initial target and do not answer "
        "the task directly. Subtask id recover-child must have suggested_mode "
        "execution_target_delegated, no dependencies, and inputs "
        '{"target_agent_id":"missing-live-cert-agent","goal":"Reply exactly '
        "REASSIGNED_CHILD_OK. This initial target is deliberately missing; if this "
        "child fails, choose reassign_exact with exact target_agent_id "
        'minimax-m2-7-highspeed.","constraints":"Return only the marker."}. '
        "Its description must repeat that recovery instruction. Subtask id "
        "validate-child must depend_on [recover-child], use suggested_mode respond, "
        "inspect the dependency result, and reply exactly REASSIGN_VALIDATED_OK only "
        "when the recovered output contains REASSIGNED_CHILD_OK; otherwise report "
        "validation failure."
    )
    from tests.e2e.cli.focus.harness.scenarios import FocusScenario

    scenario = FocusScenario(
        scenario_id="maer-live-exact-reassignment",
        prompt=prompt,
        expected_markers=(),
        timeout=600,
    )
    with focus_probe.session(rows=55, cols=180) as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_turn(session, scenario)
        write_transcript(root, scenario.scenario_id, transcript)

    state = _latest_working_state(focus_probe)
    assert state.last_result is not None
    outputs = state.last_result.outputs
    recovery = dict(outputs.get("child_recovery", {}))
    subtask_results = {
        str(item.get("subtask_id")): item
        for item in outputs.get("subtask_results", [])
        if isinstance(item, dict)
    }

    assert recovery == {
        "disposition": "reassign_exact",
        "failed_subtask_id": "recover-child",
        "target_agent_id": "minimax-m2-7-highspeed",
        "outcome": "completed",
    }
    assert "REASSIGNED_CHILD_OK" in str(subtask_results["recover-child"]["output"])
    assert "REASSIGN_VALIDATED_OK" in str(subtask_results["validate-child"]["output"])


@pytest.mark.timeout(660)
def test_live_focus_code_children_store_and_disposition_artifacts(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    """Prove live orchestration isolates child changes until parent review."""
    require_live_focus()
    root = artifact_root(tmp_path)
    repo = _create_disposable_repo(root / "scratch")
    active_probe = focus_probe.for_workdir(repo, include_project_context=False)
    prompt = (
        "Use the decompose tool exactly once with two independent subtasks. "
        "Subtask id accept-child must call file.write with exact arguments "
        '{"path": "accepted.txt", "content": "ACCEPTED_CHILD"}. '
        "Subtask id reject-child must call file.write with exact arguments "
        '{"path": "rejected.txt", "content": "REJECTED_CHILD"}. '
        "For both subtasks set suggested_mode to act and inputs to "
        f'{{"code_bearing": true, "workspace_root": "{repo}"}}. '
        "Do not create either file in the parent checkout yourself."
    )
    from tests.e2e.cli.focus.harness.scenarios import FocusScenario

    scenario = FocusScenario(
        scenario_id="maer-live-artifact-disposition",
        prompt=prompt,
        expected_markers=(),
        timeout=600,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
    )
    with active_probe.session(rows=55, cols=180) as session:
        active_probe.wait_ready(session)
        transcript = active_probe.run_turn(session, scenario)
        assert not (repo / "accepted.txt").exists()
        assert not (repo / "rejected.txt").exists()
        artifacts = _latest_public_child_artifacts(active_probe)
        by_id = {str(item.get("subtask_id")): item for item in artifacts}
        assert {"accept-child", "reject-child"}.issubset(by_id)

        accepted = active_probe.run_slash(
            session,
            "/delegate accept "
            + shlex.quote(json.dumps(by_id["accept-child"], separators=(",", ":"))),
            marker="status    accepted",
        )
        rejected = active_probe.run_slash(
            session,
            "/delegate reject "
            + shlex.quote(json.dumps(by_id["reject-child"], separators=(",", ":"))),
            marker="status    rejected",
        )
        write_transcript(
            root,
            scenario.scenario_id,
            f"{transcript}\n\n--- accept ---\n{accepted}"
            f"\n\n--- reject ---\n{rejected}\n",
        )

    assert "Delegation failed" not in accepted
    assert "Delegation failed" not in rejected
    assert (repo / "accepted.txt").read_text(encoding="utf-8").strip() == (
        "ACCEPTED_CHILD"
    )
    assert not (repo / "rejected.txt").exists()
