from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import time

import pytest

from openminion.cli.commands.daemon import daemon_start, daemon_stop
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.scenarios import FocusScenario
from tests.e2e.test_live_cli_chat_minimax_official_task_cron import (
    _trace_proves_tool_execution,
)
from tests.helpers.live_cli_chat_alibaba import framework_root, require_live_flag
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]

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
                        "to 'Reply with exactly: TLP_DELIVERED', session_target to "
                        "'isolated', and delivery mode to 'announce' with target 'last'. "
                        "Report the exact task_id."
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
            db_path,
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
