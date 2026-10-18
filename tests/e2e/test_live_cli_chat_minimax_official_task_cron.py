from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    framework_root,
    require_live_flag,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(900)]


_AGENT_ID = "minimax-m2-7"
_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)


def _cron_jobs_db(data_root: Path) -> Path:
    candidates = [
        *data_root.rglob("cron_jobs.sqlite"),
        *data_root.rglob("*.sqlite"),
        *data_root.rglob("*.db"),
    ]
    for candidate in candidates:
        try:
            with sqlite3.connect(str(candidate)) as conn:
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='cron_jobs' LIMIT 1"
                ).fetchone()
        except sqlite3.DatabaseError:
            continue
        if table is not None:
            return candidate
    return data_root / "state" / "brain" / "sessions.db"


def _read_cron_jobs(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = list(conn.execute("SELECT * FROM cron_jobs").fetchall())
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]


def _trace_proves_tool_execution(trace_root: Path, tool_name: str) -> bool:
    if not trace_root.exists():
        return False

    def _walk_for_tool_call(
        node: object, *, within_tool_arguments: bool = False
    ) -> bool:
        # `tool_calls` arrays: assistant emitted at least one tool call.
        if isinstance(node, dict):
            tool_calls = None if within_tool_arguments else node.get("tool_calls")
            if isinstance(tool_calls, list):
                for entry in tool_calls:
                    if not isinstance(entry, dict):
                        continue
                    fn = entry.get("function")
                    if isinstance(fn, dict) and fn.get("name") == tool_name:
                        return True
                    if entry.get("name") == tool_name:
                        return True
            # Tool result message: role == "tool" and name == tool_name.
            if node.get("role") == "tool" and node.get("name") == tool_name:
                return True
            for key, value in node.items():
                if _walk_for_tool_call(
                    value,
                    within_tool_arguments=within_tool_arguments or key == "arguments",
                ):
                    return True
        elif isinstance(node, list):
            for item in node:
                if _walk_for_tool_call(
                    item, within_tool_arguments=within_tool_arguments
                ):
                    return True
        return False

    def _strip_request_tools(node: object) -> object:
        if isinstance(node, dict):
            cleaned: dict[str, object] = {}
            for key, value in node.items():
                if key == "tools" and isinstance(value, list):
                    # Schema definitions in request payloads — drop entirely.
                    continue
                cleaned[key] = _strip_request_tools(value)
            return cleaned
        if isinstance(node, list):
            return [_strip_request_tools(item) for item in node]
        return node

    for trace_path in trace_root.rglob("*.json"):
        try:
            text = trace_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            continue
        cleaned = _strip_request_tools(payload)
        if _walk_for_tool_call(cleaned):
            return True
    return False


def test_trace_guard_rejects_schema_only_tools_array(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    (trace_root / "req-only-schema.json").write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "You are an assistant."},
                    {"role": "user", "content": "schedule something"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "task.schedule",
                            "description": "Schedule a task",
                            "parameters": {},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "task.list",
                            "description": "List tasks",
                            "parameters": {},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    assert not _trace_proves_tool_execution(trace_root, "task.schedule")
    assert not _trace_proves_tool_execution(trace_root, "task.list")


def test_trace_guard_passes_on_assistant_tool_call(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    (trace_root / "resp-tool-call.json").write_text(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "task.schedule",
                                        "arguments": '{"instruction":"x"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _trace_proves_tool_execution(trace_root, "task.schedule")
    # Sibling tool not called must not pass.
    assert not _trace_proves_tool_execution(trace_root, "task.list")


def test_trace_guard_passes_on_tool_result_message(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    (trace_root / "tool-result.json").write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "show tasks"},
                    {
                        "role": "tool",
                        "name": "task.list",
                        "tool_call_id": "call_2",
                        "content": '{"tasks": []}',
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _trace_proves_tool_execution(trace_root, "task.list")
    assert not _trace_proves_tool_execution(trace_root, "task.schedule")


def test_trace_guard_rejects_user_prose_mentioning_tool(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    (trace_root / "user-prose.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Use the task.schedule tool with kind=every. "
                            "After the call returns, report the task_id."
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert not _trace_proves_tool_execution(trace_root, "task.schedule")


def test_trace_guard_rejects_submit_output_describing_tool_call(
    tmp_path: Path,
) -> None:
    trace_root = tmp_path / "traces"
    trace_root.mkdir()
    (trace_root / "submit-output-nested-intent.json").write_text(
        json.dumps(
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "submit_output",
                        "arguments": {
                            "tool_calls": [
                                {
                                    "name": "task.schedule",
                                    "arguments": {"instruction": "x"},
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert not _trace_proves_tool_execution(trace_root, "task.schedule")


@pytest.mark.skipif(
    not _CONFIG.exists(),
    reason=f"missing live config: {_CONFIG}",
)
def test_tcee_04_minimax_interval_schedule_list_cancel_list_live() -> None:
    require_live_flag()

    run_id = "tcee-04-interval"
    data_root = artifact_dir() / "data-roots" / run_id
    shutil.rmtree(data_root, ignore_errors=True)
    data_root.mkdir(parents=True, exist_ok=True)

    # Step 1: schedule an interval task.
    schedule_result = run_cli_session(
        session_id_prefix=run_id + "-schedule",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use the task.schedule tool to create a recurring task that runs "
            "every 60000 ms (1 minute). Set instruction to "
            '"tcee04 interval smoke" and name to "tcee04-interval". '
            "After the tool call returns, report the task_id verbatim."
        ),
    )
    assert _trace_proves_tool_execution(schedule_result.trace_root, "task.schedule"), (
        "TCEE-04: task.schedule did not execute as a tool — no assistant "
        "tool_call entry or tool-result message named task.schedule was "
        f"found in {schedule_result.trace_root}. Transcript: "
        f"{schedule_result.transcript_path}"
    )
    db_path = _cron_jobs_db(data_root)
    rows_after_schedule = _read_cron_jobs(db_path)
    assert len(rows_after_schedule) == 1, (
        f"TCEE-04: expected 1 cron_jobs row after schedule, got "
        f"{len(rows_after_schedule)}. DB: {db_path}"
    )
    persisted_task_id = rows_after_schedule[0].get("job_id") or rows_after_schedule[
        0
    ].get("id")
    assert persisted_task_id, "TCEE-04: scheduled row missing job_id"

    # Step 2: list scheduled tasks.
    list_before_result = run_cli_session(
        session_id_prefix=run_id + "-list-before",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use the task.list tool with limit 10 to show all scheduled tasks. "
            "Then report the task_id values verbatim."
        ),
    )
    assert _trace_proves_tool_execution(list_before_result.trace_root, "task.list"), (
        "TCEE-04: task.list did not execute as a tool — no assistant "
        "tool_call entry or tool-result message named task.list was found "
        f"in {list_before_result.trace_root}. Transcript: "
        f"{list_before_result.transcript_path}"
    )

    # Step 3: cancel by exact id.
    cancel_result = run_cli_session(
        session_id_prefix=run_id + "-cancel",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            f"Use the task.cancel tool with task_id={persisted_task_id} to "
            "cancel that scheduled task. Use the exact task_id; do not "
            "abbreviate or transform it."
        ),
    )
    assert _trace_proves_tool_execution(cancel_result.trace_root, "task.cancel"), (
        "TCEE-04: task.cancel did not execute as a tool — no assistant "
        "tool_call entry or tool-result message named task.cancel was found "
        f"in {cancel_result.trace_root}. Transcript: "
        f"{cancel_result.transcript_path}"
    )
    rows_after_cancel = _read_cron_jobs(db_path)
    assert len(rows_after_cancel) == 1
    assert rows_after_cancel[0]["job_id"] == persisted_task_id
    assert int(rows_after_cancel[0].get("enabled", 0) or 0) == 0

    # Step 4: list again to confirm.
    list_after_result = run_cli_session(
        session_id_prefix=run_id + "-list-after",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use the task.list tool with limit 10 to show all scheduled tasks again."
        ),
    )
    assert _trace_proves_tool_execution(list_after_result.trace_root, "task.list"), (
        "TCEE-04: post-cancel task.list did not execute as a tool — no "
        "assistant tool_call entry or tool-result message named task.list "
        f"was found in {list_after_result.trace_root}. Transcript: "
        f"{list_after_result.transcript_path}"
    )


@pytest.mark.skipif(
    not _CONFIG.exists(),
    reason=f"missing live config: {_CONFIG}",
)
def test_tcoh_08_minimax_schedule_show_pause_list_resume_cancel_live() -> None:
    require_live_flag()

    run_id = "tcoh-08-operator-flow"
    data_root = artifact_dir() / "data-roots" / run_id
    shutil.rmtree(data_root, ignore_errors=True)
    data_root.mkdir(parents=True, exist_ok=True)

    schedule_result = run_cli_session(
        session_id_prefix=run_id + "-schedule",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use the task.schedule tool to create a recurring task that runs "
            'every 60000 ms. Set instruction to "tcoh08 operator smoke" and '
            'name to "tcoh08-operator". After the tool call returns, report '
            "the task_id verbatim."
        ),
    )
    assert _trace_proves_tool_execution(schedule_result.trace_root, "task.schedule")
    db_path = _cron_jobs_db(data_root)
    rows = _read_cron_jobs(db_path)
    assert len(rows) == 1
    persisted_task_id = rows[0].get("job_id") or rows[0].get("id")
    assert persisted_task_id

    show_result = run_cli_session(
        session_id_prefix=run_id + "-show",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            f"Use the task.show tool with task_id={persisted_task_id} and "
            "runs_limit=3. Use the exact task_id and report the returned "
            "enabled state and schedule summary."
        ),
    )
    assert _trace_proves_tool_execution(show_result.trace_root, "task.show")

    pause_result = run_cli_session(
        session_id_prefix=run_id + "-pause",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            f"Use the task.pause tool with task_id={persisted_task_id}. "
            "Use the exact task_id and do not abbreviate it."
        ),
    )
    assert _trace_proves_tool_execution(pause_result.trace_root, "task.pause")
    paused_rows = _read_cron_jobs(db_path)
    assert len(paused_rows) == 1
    assert int(paused_rows[0].get("enabled", 0) or 0) == 0

    list_result = run_cli_session(
        session_id_prefix=run_id + "-list",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use the task.list tool with limit 10 to show scheduled tasks. "
            f"Report the enabled state for task_id={persisted_task_id}."
        ),
    )
    assert _trace_proves_tool_execution(list_result.trace_root, "task.list")

    resume_result = run_cli_session(
        session_id_prefix=run_id + "-resume",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            f"Use the task.resume tool with task_id={persisted_task_id}. "
            "Use the exact task_id and report the next_due_at value."
        ),
    )
    assert _trace_proves_tool_execution(resume_result.trace_root, "task.resume")
    resumed_rows = _read_cron_jobs(db_path)
    assert len(resumed_rows) == 1
    assert int(resumed_rows[0].get("enabled", 0) or 0) == 1
    assert resumed_rows[0].get("next_due_at")

    cancel_result = run_cli_session(
        session_id_prefix=run_id + "-cancel",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=f'tool task.cancel {{"task_id":"{persisted_task_id}"}}',
    )
    assert _trace_proves_tool_execution(cancel_result.trace_root, "task.cancel")
    cancelled_rows = _read_cron_jobs(db_path)
    assert len(cancelled_rows) == 1
    assert cancelled_rows[0]["job_id"] == persisted_task_id
    assert int(cancelled_rows[0].get("enabled", 0) or 0) == 0


@pytest.mark.skipif(
    not _CONFIG.exists(),
    reason=f"missing live config: {_CONFIG}",
)
def test_taoc_minimax_operational_controls_live() -> None:
    require_live_flag()

    run_id = "taoc-operational-controls"
    data_root = artifact_dir() / "data-roots" / run_id
    shutil.rmtree(data_root, ignore_errors=True)
    data_root.mkdir(parents=True, exist_ok=True)

    schedule_result = run_cli_session(
        session_id_prefix=run_id + "-schedule",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use task.schedule exactly once to create a recurring task named "
            "'taoc-live' that runs every 600000 ms with instruction "
            "'first instruction'. Also use task.scheduler_status exactly once. "
            "Report the exact task_id and scheduler status."
        ),
    )
    assert _trace_proves_tool_execution(schedule_result.trace_root, "task.schedule")
    assert _trace_proves_tool_execution(
        schedule_result.trace_root, "task.scheduler_status"
    ), f"TAOC live scheduler status did not execute: {schedule_result.transcript}"

    db_path = _cron_jobs_db(data_root)
    scheduled_jobs = _read_cron_jobs(db_path)
    assert len(scheduled_jobs) == 1
    task_id = str(scheduled_jobs[0]["job_id"])
    schedule_revision = str(scheduled_jobs[0]["updated_at"])
    lifecycle_result = run_cli_session(
        session_id_prefix=run_id + "-lifecycle",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            f"Use task.show with exact task_id {task_id}. Then use task.update "
            f"with exact task_id {task_id}, exact expected_updated_at "
            f"'{schedule_revision}', instruction 'updated instruction', and schedule "
            "kind every with every_ms 120000. Finally use task.run_now with that "
            "exact task_id. Do not substitute or shorten either exact value."
        ),
    )
    for tool_name in ("task.show", "task.update", "task.run_now"):
        assert _trace_proves_tool_execution(lifecycle_result.trace_root, tool_name), (
            f"TAOC live lifecycle did not execute {tool_name}; transcript: "
            f"{lifecycle_result.transcript_path}"
        )

    jobs = _read_cron_jobs(db_path)
    assert len(jobs) == 1
    job = jobs[0]
    assert str(job["job_id"]) == task_id
    assert json.loads(job["schedule_json"]) == {
        "kind": "every",
        "every_ms": 120_000,
        "jitter_ms": 0,
    }
    assert json.loads(job["payload_json"])["message"] == "updated instruction"
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        runs = list(
            connection.execute(
                "SELECT * FROM cron_runs WHERE job_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        )
    assert len(runs) == 1
    assert runs[0]["state"] == "queued"

    with sqlite3.connect(str(db_path)) as connection:
        scheduler_revision = connection.execute(
            "SELECT updated_at FROM cron_scheduler_control WHERE singleton_id = 1"
        ).fetchone()
    assert scheduler_revision is not None
    scheduler_pause_result = run_cli_session(
        session_id_prefix=run_id + "-scheduler-pause",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use task.scheduler_pause with exact expected_updated_at "
            f"'{scheduler_revision[0]}' and reason 'TAOC live maintenance'. "
            "Do not substitute or shorten the exact value."
        ),
    )
    assert _trace_proves_tool_execution(
        scheduler_pause_result.trace_root, "task.scheduler_pause"
    ), f"TAOC live scheduler pause did not execute: {scheduler_pause_result.transcript}"
    with sqlite3.connect(str(db_path)) as connection:
        paused_control = connection.execute(
            "SELECT paused, reason, updated_at FROM cron_scheduler_control "
            "WHERE singleton_id = 1"
        ).fetchone()
    assert paused_control is not None
    assert paused_control[0:2] == (1, "TAOC live maintenance"), (
        f"scheduler pause was not persisted: {scheduler_pause_result.transcript}"
    )
    scheduler_resume_result = run_cli_session(
        session_id_prefix=run_id + "-scheduler-resume",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use task.scheduler_resume with exact expected_updated_at "
            f"'{paused_control[2]}'. Do not substitute or shorten that value."
        ),
    )
    assert _trace_proves_tool_execution(
        scheduler_resume_result.trace_root, "task.scheduler_resume"
    )
    with sqlite3.connect(str(db_path)) as connection:
        scheduler_control = connection.execute(
            "SELECT paused, reason FROM cron_scheduler_control WHERE singleton_id = 1"
        ).fetchone()
    assert scheduler_control == (0, None)

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE cron_runs SET state = 'finished', finished_at = updated_at "
            "WHERE job_id = ? AND state = 'queued'",
            (task_id,),
        )
        connection.commit()
    history_result = run_cli_session(
        session_id_prefix=run_id + "-history",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            f"Use task.delete_history with the exact task_id {task_id}. "
            "Approve the requested destructive action and report deleted_count."
        ),
    )
    assert _trace_proves_tool_execution(
        history_result.trace_root, "task.delete_history"
    ), (
        f"TAOC live history deletion did not execute; transcript: {history_result.transcript_path}"
    )
    with sqlite3.connect(str(db_path)) as connection:
        remaining = connection.execute(
            "SELECT COUNT(1) FROM cron_runs WHERE job_id = ?",
            (task_id,),
        ).fetchone()
    assert remaining == (0,)


@pytest.mark.skipif(
    not _CONFIG.exists(),
    reason=f"missing live config: {_CONFIG}",
)
def test_tcee_05_minimax_cron_expression_schedule_live() -> None:
    require_live_flag()

    run_id = "tcee-05-cron-expr"
    data_root = artifact_dir() / "data-roots" / run_id
    shutil.rmtree(data_root, ignore_errors=True)
    data_root.mkdir(parents=True, exist_ok=True)

    schedule_result = run_cli_session(
        session_id_prefix=run_id + "-schedule",
        agent_id=_AGENT_ID,
        config_path=_CONFIG,
        data_root_override=data_root,
        auto_confirm=True,
        user_input=(
            "Use the task.schedule tool to create a cron-expression task. "
            'Set schedule to {"kind": "cron", "expr": "*/5 * * * *", '
            '"tz": "UTC"}. Set instruction to "tcee05 cron expr smoke" and '
            'name to "tcee05-cron-expr". After the tool call returns, '
            "report the task_id verbatim."
        ),
    )
    assert _trace_proves_tool_execution(schedule_result.trace_root, "task.schedule"), (
        "TCEE-05: task.schedule did not execute as a tool — no assistant "
        "tool_call entry or tool-result message named task.schedule was "
        f"found in {schedule_result.trace_root}. Transcript: "
        f"{schedule_result.transcript_path}"
    )

    db_path = _cron_jobs_db(data_root)
    rows = _read_cron_jobs(db_path)
    assert len(rows) == 1, (
        f"TCEE-05: expected 1 cron_jobs row after cron-expression schedule, "
        f"got {len(rows)}. DB: {db_path}"
    )
    row = rows[0]
    schedule_blob = (
        row.get("schedule") or row.get("schedule_json") or row.get("payload") or "{}"
    )
    schedule_data = (
        json.loads(schedule_blob) if isinstance(schedule_blob, str) else schedule_blob
    )
    # Anti-cheat: schedule must persist as cron with the exact expression
    # we asked for. Anything else means runtime semantic translation snuck in.
    assert schedule_data.get("kind") == "cron", (
        f"TCEE-05: schedule.kind != cron in persisted row. Got: {schedule_data}"
    )
    assert schedule_data.get("expr") == "*/5 * * * *", (
        f"TCEE-05: schedule.expr does not match input. Got: {schedule_data}"
    )
    assert (schedule_data.get("tz") or schedule_data.get("timezone")) in {
        "UTC",
        None,
    }, f"TCEE-05: unexpected timezone in persisted schedule: {schedule_data}"
    assert row.get("next_due_at") or row.get("next_run_at"), (
        f"TCEE-05: persisted row missing next_due_at / next_run_at. Row: {row}"
    )
