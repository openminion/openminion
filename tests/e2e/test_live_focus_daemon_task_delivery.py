from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

import pytest

from openminion.cli.commands.daemon import (
    build_daemon_status_payload,
    daemon_start,
    daemon_stop,
)
from openminion.modules.task.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.scenarios import FocusScenario
from tests.e2e.test_live_cli_chat_minimax_official_task_cron import (
    _trace_proves_tool_execution,
)
from tests.helpers.live_cli_chat_alibaba import framework_root, require_live_flag
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(900)]

_AGENT_ID = "minimax-m2-7"
_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)


def _isolated_config(run_root: Path) -> Path:
    payload = json.loads(_CONFIG.read_text(encoding="utf-8"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        payload.setdefault("runtime", {})["ipc_port"] = listener.getsockname()[1]
    payload["runtime"]["tool_workspace_root"] = str(run_root)
    payload["runtime"]["provider_retry_max_attempts"] = 1
    path = run_root / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def _rows(db_path: Path, query: str, params: tuple[object, ...] = ()) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def _wait_for_finished_run(db_path: Path, job_id: str) -> dict:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        runs = _rows(
            db_path,
            "SELECT * FROM cron_runs WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        )
        if runs and runs[0]["state"] in {
            "finished",
            "failed",
            "cancelled",
            "timed_out",
        }:
            return runs[0]
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for cron run for {job_id}")


def _wait_for_finished_run_count(
    db_path: Path,
    job_id: str,
    expected: int,
) -> list[dict]:
    deadline = time.monotonic() + 180
    terminal = {"finished", "failed", "cancelled", "timed_out"}
    while time.monotonic() < deadline:
        runs = _rows(
            db_path,
            "SELECT * FROM cron_runs WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        )
        if len(runs) >= expected and all(row["state"] in terminal for row in runs):
            return runs
        time.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {expected} finished cron runs for {job_id}"
    )


def _wait_for_running_run_count(
    db_path: Path, job_id: str, expected: int
) -> list[dict]:
    deadline = time.monotonic() + 180
    terminal = {"finished", "failed", "cancelled", "timed_out"}
    while time.monotonic() < deadline:
        runs = _rows(
            db_path,
            "SELECT * FROM cron_runs WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        )
        if len(runs) > expected:
            raise AssertionError(f"observed unexpected run {len(runs)} for {job_id}")
        if len(runs) == expected:
            if runs[-1]["state"] == "running":
                return runs
            if runs[-1]["state"] in terminal:
                raise AssertionError(
                    f"run {expected} finished before task pause for {job_id}"
                )
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for running run {expected} for {job_id}")


def _scheduled_task_id(data_root: Path, job_id: str) -> str:
    rows = _rows(
        data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH,
        "SELECT task_id FROM scheduled_tasks WHERE cron_job_id = ?",
        (job_id,),
    )
    assert len(rows) == 1
    return str(rows[0]["task_id"])


def _run_task_action(
    *,
    python_bin: Path,
    openminion_root: Path,
    config_path: Path,
    home_root: Path,
    data_root: Path,
    session_id: str,
    action: str,
    task_id: str,
) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(openminion_root / "src")
    completed = subprocess.run(  # noqa: S603 - fixed local E2E argv
        [
            str(python_bin),
            "-m",
            "openminion",
            "--config",
            str(config_path),
            "--home-root",
            str(home_root),
            "--data-root",
            str(data_root),
            "--agent",
            _AGENT_ID,
            "tasks",
            action,
            task_id,
            "--agent-id",
            _AGENT_ID,
            "--session",
            session_id,
            "--json",
        ],
        cwd=openminion_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    return payload


def _http_request_count(trace_root: Path) -> int:
    count = 0
    for path in trace_root.rglob("*-http.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("artifact_kind") == "http_request":
            count += 1
    return count


def _assert_focus_final_answer_reports_task_id(transcript: str, task_id: str) -> None:
    done_at = transcript.rfind("Done in")
    answer_at = transcript.rfind("●", 0, done_at)
    assert answer_at >= 0 and done_at > answer_at
    assert task_id in transcript[answer_at:done_at]


def _trace_tool_call_arguments(trace_root: Path, tool_name: str) -> list[dict]:
    arguments: list[dict] = []

    def collect(node: object, *, within_arguments: bool = False) -> None:
        if isinstance(node, dict):
            calls = None if within_arguments else node.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if isinstance(function, dict) and function.get("name") == tool_name:
                        raw = function.get("arguments")
                    elif call.get("name") == tool_name:
                        raw = call.get("arguments")
                    else:
                        continue
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(parsed, dict):
                        arguments.append(parsed)
            for key, value in node.items():
                if key != "tools":
                    collect(
                        value, within_arguments=within_arguments or key == "arguments"
                    )
        elif isinstance(node, list):
            for item in node:
                collect(item, within_arguments=within_arguments)

    for trace_path in trace_root.rglob("*.json"):
        collect(json.loads(trace_path.read_text(encoding="utf-8")))
    return arguments


def _assert_daemon_stopped(
    config_path: Path, *, home_root: Path, data_root: Path, expected_pid: int
) -> None:
    status = build_daemon_status_payload(
        str(config_path), home_root=home_root, data_root=data_root
    )
    assert status["pid"] in {None, expected_pid}
    assert status["pid_alive"] is False
    assert status["reachable"] is False


def _cron_announcements(
    runtime_db_path: Path,
    *,
    session_id: str,
    conversation_id: str,
) -> tuple[list[dict], list[dict]]:
    outbound = _rows(
        runtime_db_path,
        "SELECT * FROM messages WHERE session_id = ? AND role = 'outbound' "
        "AND conversation_id = ?",
        (session_id, conversation_id),
    )
    messages = [
        row
        for row in outbound
        if json.loads(row["metadata_json"]).get("cron_announce") == "true"
    ]
    events = _rows(
        runtime_db_path,
        "SELECT * FROM events WHERE session_id = ? AND event_type = 'cron.announce'",
        (session_id,),
    )
    return messages, events


def _trace_proves_file_read(
    trace_root: Path,
    *,
    session_id: str,
    relative_path: str,
) -> bool:
    expected_file = (trace_root.parent / relative_path).resolve()

    def matching_call(node: object, *, within_arguments: bool = False) -> bool:
        if isinstance(node, dict):
            calls = None if within_arguments else node.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if call.get("name") == "file.read":
                        arguments = call.get("arguments")
                    elif (
                        isinstance(function, dict)
                        and function.get("name") == "file.read"
                    ):
                        arguments = function.get("arguments")
                    else:
                        continue
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            continue
                    if isinstance(arguments, dict):
                        observed = str(arguments.get("path") or "").strip()
                        if observed == relative_path:
                            return True
                        observed_path = Path(observed)
                        if (
                            observed_path.is_absolute()
                            and observed_path.resolve() == expected_file
                        ):
                            return True
            return any(
                matching_call(
                    value, within_arguments=within_arguments or key == "arguments"
                )
                for key, value in node.items()
            )
        if isinstance(node, list):
            return any(
                matching_call(item, within_arguments=within_arguments) for item in node
            )
        return False

    for trace_path in trace_root.rglob("*.json"):
        try:
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        trace = payload.get("trace") if isinstance(payload, dict) else None
        if not isinstance(trace, dict):
            continue
        trace_session_id = str(trace.get("session_id") or "")
        if trace_session_id != session_id and not trace_session_id.startswith(
            f"{session_id}::"
        ):
            continue
        if matching_call(payload):
            return True
    return False


def test_trace_file_read_matches_exact_workspace_file_identity(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    trace_path = trace_root / "trace.json"

    def write_trace(path: str) -> None:
        trace_path.write_text(
            json.dumps(
                {
                    "trace": {"session_id": "watch:1::conv:1"},
                    "response": {
                        "tool_calls": [
                            {
                                "name": "file.read",
                                "arguments": {"path": path},
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    write_trace("release-status.json")
    assert _trace_proves_file_read(
        trace_root,
        session_id="watch:1",
        relative_path="release-status.json",
    )

    write_trace(str(tmp_path / "release-status.json"))
    assert _trace_proves_file_read(
        trace_root,
        session_id="watch:1",
        relative_path="release-status.json",
    )

    write_trace(str(tmp_path / "other.json"))
    assert not _trace_proves_file_read(
        trace_root,
        session_id="watch:1",
        relative_path="release-status.json",
    )


def test_trace_tool_call_arguments_reads_normalized_trace(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    (trace_root / "call.json").write_text(
        json.dumps(
            {
                "tool_calls": [
                    {
                        "name": "task.schedule",
                        "arguments": {"schedule": {"kind": "at", "after_seconds": 90}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _trace_tool_call_arguments(trace_root, "task.schedule") == [
        {"schedule": {"kind": "at", "after_seconds": 90}}
    ]


def test_task_id_assertion_uses_final_focus_answer() -> None:
    transcript = (
        "● I will schedule it.\n"
        "tool result task_id: tool-card-id\n"
        "● Scheduled task_id: final-answer-id\n"
        "  Done in 2s\n"
    )

    _assert_focus_final_answer_reports_task_id(transcript, "final-answer-id")
    with pytest.raises(AssertionError):
        _assert_focus_final_answer_reports_task_id(transcript, "tool-card-id")


@pytest.mark.skipif(not _CONFIG.exists(), reason=f"missing live config: {_CONFIG}")
def test_official_minimax_focus_daemon_preserves_conversation_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_live_flag()
    openminion_root = Path(__file__).resolve().parents[2]
    python_bin = Path(
        os.getenv("OPENMINION_PYTHON", openminion_root / ".venv/bin/python3.11")
    )
    run_root = tmp_path / "focus-daemon-task"
    data_root = run_root / "data"
    trace_root = run_root / "traces"
    data_root.mkdir(parents=True)
    trace_root.mkdir(parents=True)
    config_path = _isolated_config(run_root)
    session_id = "tlp-focus-daemon-origin"
    conversation_id = f"focus-{session_id}"
    home_root = run_root / "home"
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root(),
        data_root=data_root,
        config_path=config_path,
        agent_id=_AGENT_ID,
        workdir=openminion_root,
        session_id=session_id,
        include_project_context=False,
        allow_unsandboxed_exec=False,
    )
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(trace_root))
    due_at = datetime.now(timezone.utc) + timedelta(seconds=90)
    task_name = "tlp-focus-daemon-conversation-delivery"

    assert daemon_start(str(config_path), home_root=home_root, data_root=data_root) == 0
    daemon_pid = int(
        build_daemon_status_payload(
            str(config_path), home_root=home_root, data_root=data_root
        )["pid"]
    )
    try:
        with probe.session(rows=52, cols=180) as session:
            probe.wait_ready(session)
            probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="tlp-focus-daemon-delivery",
                    prompt=(
                        "Use task.schedule to create a one-time task at exactly "
                        f"{due_at.isoformat()}. Set name to {task_name!r}, instruction "
                        "to 'Reply with exactly: TLP_DELIVERED', and report the exact "
                        "task_id."
                    ),
                    expected_markers=(),
                    requires_approval=True,
                    max_auto_approvals=3,
                    approval_reply="session",
                    timeout=240,
                ),
            )

        db_path = data_root / "state" / "brain" / "sessions.db"
        jobs = _rows(
            db_path,
            "SELECT * FROM cron_jobs WHERE name = ?",
            (task_name,),
        )
        assert len(jobs) == 1
        job = jobs[0]
        payload = json.loads(job["payload_json"])
        origin = payload["_openminion_origin"]
        assert origin["session_id"] == session_id
        assert origin["conversation_id"] == conversation_id
        assert _trace_proves_tool_execution(trace_root, "task.schedule")

        run = _wait_for_finished_run(db_path, job["job_id"])
        runs = _rows(
            db_path,
            "SELECT * FROM cron_runs WHERE job_id = ?",
            (job["job_id"],),
        )
        assert [row["run_id"] for row in runs] == [run["run_id"]]
        assert run["state"] == "finished"
        assert run["error_json"] is None
        assert run["isolated_session_id"]
        assert json.loads(run["delivery_targets_json"]) == ["announce:last"]
        delivery = json.loads(run["output_json"])["delivery"]
        assert delivery == {
            "mode": "announce",
            "state": "succeeded",
            "targets": ["announce:last"],
        }
        completed_task = _rows(
            data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH,
            "SELECT state FROM scheduled_tasks WHERE cron_job_id = ?",
            (job["job_id"],),
        )
        assert completed_task == [{"state": "done"}]
        completed_job = _rows(
            db_path,
            "SELECT enabled FROM cron_jobs WHERE job_id = ?",
            (job["job_id"],),
        )
        assert completed_job == [{"enabled": 0}]

        runtime_db_path = data_root / "state" / "openminion.db"
        outbound = _rows(
            runtime_db_path,
            "SELECT * FROM messages WHERE session_id = ? AND role = 'outbound' "
            "AND conversation_id = ?",
            (session_id, conversation_id),
        )
        cron_delivery = [
            row
            for row in outbound
            if json.loads(row["metadata_json"]).get("cron_announce") == "true"
        ]
        assert len(cron_delivery) == 1
        assert "TLP_DELIVERED" in cron_delivery[0]["body"]
        events = _rows(
            runtime_db_path,
            "SELECT * FROM events WHERE session_id = ? "
            "AND event_type = 'cron.announce'",
            (session_id,),
        )
        assert len(events) == 1
        event_payload = json.loads(events[0]["payload_json"])
        assert event_payload["conversation_id"] == conversation_id
        assert event_payload["cron_run_id"] == run["run_id"]
    finally:
        daemon_stop(str(config_path), home_root=home_root, data_root=data_root)
        _assert_daemon_stopped(
            config_path,
            home_root=home_root,
            data_root=data_root,
            expected_pid=daemon_pid,
        )


@pytest.mark.skipif(not _CONFIG.exists(), reason=f"missing live config: {_CONFIG}")
def test_official_minimax_focus_daemon_material_change_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_live_flag()
    openminion_root = Path(__file__).resolve().parents[2]
    python_bin = Path(
        os.getenv("OPENMINION_PYTHON", openminion_root / ".venv/bin/python3.11")
    )
    run_root = tmp_path / "focus-daemon-watch"
    data_root = run_root / "data"
    trace_root = run_root / "traces"
    data_root.mkdir(parents=True)
    trace_root.mkdir(parents=True)
    fixture_path = run_root / "release-status.json"
    fixture_path.write_text(
        json.dumps({"release": "cedar", "status": "ready", "checked_at": "t0"}),
        encoding="utf-8",
    )
    config_path = _isolated_config(run_root)
    session_id = "twmc-focus-daemon-origin"
    conversation_id = f"focus-{session_id}"
    home_root = run_root / "home"
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root(),
        data_root=data_root,
        config_path=config_path,
        agent_id=_AGENT_ID,
        workdir=run_root,
        session_id=session_id,
        include_project_context=False,
        allow_unsandboxed_exec=False,
    )
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(trace_root))
    watch_name = "twmc-release-status-material-change"
    daemon_running = False
    daemon_pid = 0

    assert daemon_start(str(config_path), home_root=home_root, data_root=data_root) == 0
    daemon_running = True
    daemon_pid = int(
        build_daemon_status_payload(
            str(config_path), home_root=home_root, data_root=data_root
        )["pid"]
    )
    try:
        with probe.session(rows=58, cols=180) as session:
            probe.wait_ready(session)
            turn_transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="twmc-focus-daemon-material-change",
                    prompt=(
                        "Use task.watch to create a watch named by description "
                        f"{watch_name!r}. Every 1 minute, read release-status.json "
                        "from the current workspace. The alert condition is exactly "
                        "that the JSON field status is not 'ready'; changes to "
                        "checked_at alone are not material. Use at most 4 checks, "
                        "a 10 minute TTL, a 60 second check timeout, delivery "
                        "'announce', and stop_on_condition true. Do not configure an "
                        "action and do not authorize writes. Report the exact task_id."
                    ),
                    expected_markers=(),
                    requires_approval=True,
                    max_auto_approvals=3,
                    approval_reply="session",
                    timeout=240,
                ),
            )
            db_path = data_root / "state" / "brain" / "sessions.db"
            jobs = _rows(
                db_path,
                "SELECT * FROM cron_jobs WHERE name = ?",
                (watch_name,),
            )
            assert len(jobs) == 1
            job = jobs[0]
            job_id = str(job["job_id"])
            task_id = _scheduled_task_id(data_root, job_id)
            _assert_focus_final_answer_reports_task_id(turn_transcript, task_id)

        payload = json.loads(job["payload_json"])
        watch = payload["_openminion_watch"]
        assert watch["interval_minutes"] == 1
        assert watch["max_checks"] == 4
        assert watch["ttl_minutes"] == 10
        assert watch["timeout_seconds"] == 60
        assert watch["stop_on_condition"] is True
        assert watch["on_condition_action"] is None
        assert watch["write_authorized"] is False
        assert (
            json.loads(config_path.read_text(encoding="utf-8"))["runtime"][
                "provider_retry_max_attempts"
            ]
            == 1
        )
        assert payload["_openminion_origin"]["session_id"] == session_id
        assert _trace_proves_tool_execution(trace_root, "task.watch")

        runtime_db_path = data_root / "state" / "openminion.db"
        first_two = _wait_for_finished_run_count(db_path, job_id, 1)
        first_output = json.loads(first_two[0]["output_json"])
        assert first_two[0]["state"] == "finished"
        assert first_output["watch_condition_valid"] is True
        assert first_output["watch_condition_met"] is False
        assert first_output["watch_delivery_requested"] is False
        assert _trace_proves_file_read(
            trace_root,
            session_id=str(first_two[0]["isolated_session_id"]),
            relative_path="release-status.json",
        )
        assert _cron_announcements(
            runtime_db_path,
            session_id=session_id,
            conversation_id=conversation_id,
        ) == ([], [])

        fixture_path.write_text(
            json.dumps({"release": "cedar", "status": "ready", "checked_at": "t1"}),
            encoding="utf-8",
        )
        _wait_for_running_run_count(db_path, job_id, 2)
        paused = _run_task_action(
            python_bin=python_bin,
            openminion_root=openminion_root,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            session_id=f"{session_id}-control",
            action="pause",
            task_id=task_id,
        )
        assert paused["enabled"] is False
        first_two = _wait_for_finished_run_count(db_path, job_id, 2)
        assert len(first_two) == 2
        second_output = json.loads(first_two[1]["output_json"])
        assert second_output["watch_condition_valid"] is True
        assert second_output["watch_condition_met"] is False
        assert second_output["watch_delivery_requested"] is False
        assert _trace_proves_file_read(
            trace_root,
            session_id=str(first_two[1]["isolated_session_id"]),
            relative_path="release-status.json",
        )
        assert _cron_announcements(
            runtime_db_path,
            session_id=session_id,
            conversation_id=conversation_id,
        ) == ([], [])

        assert (
            daemon_stop(str(config_path), home_root=home_root, data_root=data_root) == 0
        )
        daemon_running = False
        _assert_daemon_stopped(
            config_path,
            home_root=home_root,
            data_root=data_root,
            expected_pid=daemon_pid,
        )
        before_restart = _rows(
            db_path,
            "SELECT job_id FROM cron_jobs WHERE name = ?",
            (watch_name,),
        )
        assert before_restart == [{"job_id": job_id}]
        fixture_path.write_text(
            json.dumps({"release": "cedar", "status": "blocked", "checked_at": "t2"}),
            encoding="utf-8",
        )
        resumed = _run_task_action(
            python_bin=python_bin,
            openminion_root=openminion_root,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            session_id=f"{session_id}-control",
            action="resume",
            task_id=task_id,
        )
        assert resumed["enabled"] is True
        assert (
            daemon_start(str(config_path), home_root=home_root, data_root=data_root)
            == 0
        )
        daemon_running = True
        daemon_pid = int(
            build_daemon_status_payload(
                str(config_path), home_root=home_root, data_root=data_root
            )["pid"]
        )

        runs = _wait_for_finished_run_count(db_path, job_id, 3)
        third_output = json.loads(runs[2]["output_json"])
        assert third_output["watch_condition_valid"] is True
        assert third_output["watch_condition_met"] is True
        assert third_output["watch_delivery_requested"] is True
        assert third_output["watch_terminal"] is True
        assert third_output["watch_terminal_reason"] == "condition_met"
        assert _trace_proves_file_read(
            trace_root,
            session_id=str(runs[2]["isolated_session_id"]),
            relative_path="release-status.json",
        )

        completed_task = _rows(
            data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH,
            "SELECT state, cron_job_id FROM scheduled_tasks WHERE cron_job_id = ?",
            (job_id,),
        )
        assert completed_task == [{"state": "done", "cron_job_id": job_id}]
        completed_job = _rows(
            db_path,
            "SELECT enabled, payload_json FROM cron_jobs WHERE job_id = ?",
            (job_id,),
        )
        assert len(completed_job) == 1
        assert completed_job[0]["enabled"] == 0
        final_watch = json.loads(completed_job[0]["payload_json"])["_openminion_watch"]
        assert final_watch["checks_completed"] == 3
        assert final_watch["last_condition_met"] is True
        assert final_watch["last_terminal_reason"] == "condition_met"

        messages, events = _cron_announcements(
            runtime_db_path,
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert len(messages) == 1
        assert len(events) == 1
        assert (
            json.loads(messages[0]["metadata_json"])["cron_run_id"] == runs[2]["run_id"]
        )
        assert json.loads(events[0]["payload_json"])["cron_run_id"] == runs[2]["run_id"]

        time.sleep(65)
        assert (
            len(
                _rows(
                    db_path, "SELECT run_id FROM cron_runs WHERE job_id = ?", (job_id,)
                )
            )
            == 3
        )
        final_messages, final_events = _cron_announcements(
            runtime_db_path,
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert len(final_messages) == 1
        assert len(final_events) == 1
        assert 0 < _http_request_count(trace_root) <= 32
    finally:
        if daemon_running:
            daemon_stop(str(config_path), home_root=home_root, data_root=data_root)
            _assert_daemon_stopped(
                config_path,
                home_root=home_root,
                data_root=data_root,
                expected_pid=daemon_pid,
            )


@pytest.mark.skipif(not _CONFIG.exists(), reason=f"missing live config: {_CONFIG}")
def test_official_minimax_focus_daemon_ordinary_recurring_two_deliveries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_live_flag()
    openminion_root = Path(__file__).resolve().parents[2]
    python_bin = Path(
        os.getenv("OPENMINION_PYTHON", openminion_root / ".venv/bin/python3.11")
    )
    run_root = tmp_path / "focus-daemon-recurring"
    data_root = run_root / "data"
    trace_root = run_root / "traces"
    data_root.mkdir(parents=True)
    trace_root.mkdir(parents=True)
    config_path = _isolated_config(run_root)
    session_id = "tsrl-focus-daemon-recurring"
    conversation_id = f"focus-{session_id}"
    home_root = run_root / "home"
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root(),
        data_root=data_root,
        config_path=config_path,
        agent_id=_AGENT_ID,
        workdir=run_root,
        session_id=session_id,
        include_project_context=False,
        allow_unsandboxed_exec=False,
    )
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(trace_root))
    task_name = "tsrl-ordinary-recurring-two-deliveries"
    daemon_running = False
    daemon_pid = 0

    assert daemon_start(str(config_path), home_root=home_root, data_root=data_root) == 0
    daemon_running = True
    daemon_pid = int(
        build_daemon_status_payload(
            str(config_path), home_root=home_root, data_root=data_root
        )["pid"]
    )
    try:
        with probe.session(rows=52, cols=180) as session:
            probe.wait_ready(session)
            turn_transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="tsrl-focus-daemon-ordinary-recurring",
                    prompt=(
                        "Use task.schedule to create a recurring task named "
                        f"{task_name!r}. Set schedule to exactly "
                        '{"kind":"every","every_ms":60000}. Set instruction '
                        "'Reply with exactly: TSRL_RECURRING_DELIVERY'. Keep the "
                        "default announce delivery and report the exact task_id."
                    ),
                    expected_markers=(),
                    requires_approval=True,
                    max_auto_approvals=3,
                    approval_reply="session",
                    timeout=240,
                ),
            )
            db_path = data_root / "state" / "brain" / "sessions.db"
            jobs = _rows(
                db_path, "SELECT * FROM cron_jobs WHERE name = ?", (task_name,)
            )
            assert len(jobs) == 1
            job = jobs[0]
            job_id = str(job["job_id"])
            task_id = _scheduled_task_id(data_root, job_id)
            _assert_focus_final_answer_reports_task_id(turn_transcript, task_id)

        assert json.loads(job["schedule_json"]) == {
            "kind": "every",
            "every_ms": 60_000,
            "jitter_ms": 0,
        }
        assert _trace_proves_tool_execution(trace_root, "task.schedule")

        first = _wait_for_finished_run_count(db_path, job_id, 1)
        assert len(first) == 1
        _wait_for_running_run_count(db_path, job_id, 2)
        paused = _run_task_action(
            python_bin=python_bin,
            openminion_root=openminion_root,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            session_id=f"{session_id}-control",
            action="pause",
            task_id=task_id,
        )
        assert paused["enabled"] is False
        runs = _wait_for_finished_run_count(db_path, job_id, 2)
        assert len(runs) == 2
        assert len({row["run_id"] for row in runs}) == 2
        assert (
            datetime.fromisoformat(runs[1]["due_at"])
            - datetime.fromisoformat(runs[0]["due_at"])
        ) == timedelta(seconds=60)
        assert all(row["state"] == "finished" for row in runs)
        assert all(
            json.loads(row["delivery_targets_json"]) == ["announce:last"]
            for row in runs
        )

        assert (
            daemon_stop(str(config_path), home_root=home_root, data_root=data_root) == 0
        )
        daemon_running = False
        _assert_daemon_stopped(
            config_path,
            home_root=home_root,
            data_root=data_root,
            expected_pid=daemon_pid,
        )
        assert (
            len(_rows(db_path, "SELECT * FROM cron_runs WHERE job_id = ?", (job_id,)))
            == 2
        )

        messages, events = _cron_announcements(
            data_root / "state" / "openminion.db",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert len(messages) == 2
        assert len(events) == 2
        assert {
            json.loads(row["metadata_json"])["cron_run_id"] for row in messages
        } == {row["run_id"] for row in runs}
        assert {json.loads(row["payload_json"])["cron_run_id"] for row in events} == {
            row["run_id"] for row in runs
        }
        assert all("TSRL_RECURRING_DELIVERY" in row["body"] for row in messages)
        assert 0 < _http_request_count(trace_root) <= 24

        cancelled = _run_task_action(
            python_bin=python_bin,
            openminion_root=openminion_root,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            session_id=f"{session_id}-control",
            action="cancel",
            task_id=task_id,
        )
        assert cancelled["cancelled"] is True
    finally:
        if daemon_running:
            daemon_stop(str(config_path), home_root=home_root, data_root=data_root)
            _assert_daemon_stopped(
                config_path,
                home_root=home_root,
                data_root=data_root,
                expected_pid=daemon_pid,
            )


@pytest.mark.skipif(not _CONFIG.exists(), reason=f"missing live config: {_CONFIG}")
def test_official_minimax_focus_daemon_relative_one_time_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_live_flag()
    openminion_root = Path(__file__).resolve().parents[2]
    python_bin = Path(
        os.getenv("OPENMINION_PYTHON", openminion_root / ".venv/bin/python3.11")
    )
    run_root = tmp_path / "focus-daemon-relative-one-time"
    data_root = run_root / "data"
    trace_root = run_root / "traces"
    data_root.mkdir(parents=True)
    trace_root.mkdir(parents=True)
    config_path = _isolated_config(run_root)
    session_id = "tsrl-focus-daemon-relative-one-time"
    conversation_id = f"focus-{session_id}"
    home_root = run_root / "home"
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root(),
        data_root=data_root,
        config_path=config_path,
        agent_id=_AGENT_ID,
        workdir=run_root,
        session_id=session_id,
        include_project_context=False,
        allow_unsandboxed_exec=False,
    )
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(trace_root))
    task_name = "tsrl-relative-one-time-delivery"

    assert daemon_start(str(config_path), home_root=home_root, data_root=data_root) == 0
    daemon_pid = int(
        build_daemon_status_payload(
            str(config_path), home_root=home_root, data_root=data_root
        )["pid"]
    )
    try:
        with probe.session(rows=52, cols=180) as session:
            probe.wait_ready(session)
            turn_transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="tsrl-focus-daemon-relative-one-time",
                    prompt=(
                        "Use task.schedule to create a one-time task named "
                        f"{task_name!r} exactly 90 seconds after task.schedule "
                        "executes. Use schedule kind 'at' with after_seconds 90, "
                        "instruction 'Reply with exactly: TSRL_RELATIVE_DELIVERY', "
                        "and report the exact task_id."
                    ),
                    expected_markers=(),
                    requires_approval=True,
                    max_auto_approvals=3,
                    approval_reply="session",
                    timeout=240,
                ),
            )
            db_path = data_root / "state" / "brain" / "sessions.db"
            jobs = _rows(
                db_path, "SELECT * FROM cron_jobs WHERE name = ?", (task_name,)
            )
            assert len(jobs) == 1
            job = jobs[0]
            job_id = str(job["job_id"])
            task_id = _scheduled_task_id(data_root, job_id)
            _assert_focus_final_answer_reports_task_id(turn_transcript, task_id)

        schedule = json.loads(job["schedule_json"])
        schedule_at = datetime.fromisoformat(schedule["at"])
        next_due_at = datetime.fromisoformat(job["next_due_at"])
        created_at = datetime.fromisoformat(job["created_at"])
        assert schedule_at == next_due_at
        assert abs((schedule_at - created_at).total_seconds() - 90) <= 2
        assert _trace_proves_tool_execution(trace_root, "task.schedule")
        schedule_calls = _trace_tool_call_arguments(trace_root, "task.schedule")
        assert schedule_calls
        assert all(
            arguments.get("schedule") == {"kind": "at", "after_seconds": 90}
            for arguments in schedule_calls
        )

        run = _wait_for_finished_run(db_path, job_id)
        assert run["state"] == "finished"
        assert datetime.fromisoformat(run["due_at"]) == schedule_at
        assert json.loads(run["delivery_targets_json"]) == ["announce:last"]
        assert (
            len(_rows(db_path, "SELECT * FROM cron_runs WHERE job_id = ?", (job_id,)))
            == 1
        )
        assert _rows(
            data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH,
            "SELECT state FROM scheduled_tasks WHERE cron_job_id = ?",
            (job_id,),
        ) == [{"state": "done"}]
        assert _rows(
            db_path,
            "SELECT enabled, next_due_at FROM cron_jobs WHERE job_id = ?",
            (job_id,),
        ) == [{"enabled": 0, "next_due_at": None}]

        messages, events = _cron_announcements(
            data_root / "state" / "openminion.db",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert len(messages) == 1
        assert len(events) == 1
        assert "TSRL_RELATIVE_DELIVERY" in messages[0]["body"]
        assert json.loads(messages[0]["metadata_json"])["cron_run_id"] == run["run_id"]
        assert json.loads(events[0]["payload_json"])["cron_run_id"] == run["run_id"]
        assert 0 < _http_request_count(trace_root) <= 24
    finally:
        daemon_stop(str(config_path), home_root=home_root, data_root=data_root)
        _assert_daemon_stopped(
            config_path,
            home_root=home_root,
            data_root=data_root,
            expected_pid=daemon_pid,
        )
