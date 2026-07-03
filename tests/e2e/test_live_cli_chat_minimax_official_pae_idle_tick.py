from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    framework_root,
    require_live_flag,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(420)]


_AGENT_ID = "minimax-m2-7"
_PAE_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official-pae.json",
    framework_root(),
)

_PROBE_MESSAGE = (
    "I want to verify the proactive autonomous entrypoint. Use the `plan` "
    'tool with action="declare" to create a plan with plan_id '
    '"pae-verify" and exactly three steps: step 1 "checkpoint one", '
    'step 2 "checkpoint two", step 3 "checkpoint three". Set '
    "continue_plan_autonomously=true. Immediately after declaring, call "
    'the `plan` tool again with action="complete" and plan_id '
    '"pae-verify" to mark the plan done. Do this in a single turn — '
    "declare then complete — without asking me any questions."
)


def _session_events(state_db: Path, *, event_types: tuple[str, ...]) -> list[dict]:
    if not state_db.exists():
        return []
    placeholders = ",".join("?" for _ in event_types)
    with sqlite3.connect(str(state_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT event_id, seq, timestamp, event_type, payload_json
            FROM session_events
            WHERE event_type IN ({placeholders})
            ORDER BY seq ASC
            """,
            event_types,
        ).fetchall()
    return [dict(row) for row in rows]


def _cron_jobs(state_db: Path, *, kind_filter: str = "agentIdleTick") -> list[dict]:
    if not state_db.exists():
        return []
    with sqlite3.connect(str(state_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT job_id, agent_id, session_target, payload_json, enabled
            FROM cron_jobs
            WHERE payload_json LIKE ?
            """,
            (f'%"kind": "{kind_filter}"%',),
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.e2e
def test_live_minimax_m2_7_pae_idle_tick_schedule_and_cancel() -> None:
    require_live_flag()
    if not _PAE_CONFIG.exists():
        pytest.skip(f"missing config file: {_PAE_CONFIG}")

    run_id = f"pae-verify-{int(time.time())}"
    data_root = artifact_dir() / "data-roots" / run_id

    result = run_cli_session(
        session_id_prefix=run_id,
        user_input=f"{_PROBE_MESSAGE}\n/exit\n",
        agent_id=_AGENT_ID,
        config_path=_PAE_CONFIG,
        data_root_override=data_root,
    )

    state_db = data_root / "state" / "brain" / "sessions.db"
    events = _session_events(
        state_db,
        event_types=(
            "pae.idle_tick.scheduled",
            "pae.idle_tick.cancelled",
            "pae.idle_tick.suppressed",
            "task_plan.declared",
            "task_plan.completed",
            "task_plan.abandoned",
        ),
    )
    by_type: dict[str, list[dict]] = {}
    for event in events:
        by_type.setdefault(str(event["event_type"]), []).append(event)

    scheduled = by_type.get("pae.idle_tick.scheduled", [])
    cancelled = by_type.get("pae.idle_tick.cancelled", [])
    suppressed = by_type.get("pae.idle_tick.suppressed", [])
    plan_declared = by_type.get("task_plan.declared", [])
    plan_completed = by_type.get("task_plan.completed", [])
    plan_abandoned = by_type.get("task_plan.abandoned", [])

    failure_diag = (
        f"transcript={result.transcript_path}\n"
        f"state_db={state_db}\n"
        "event_counts="
        + json.dumps({k: len(v) for k, v in by_type.items()}, indent=2, sort_keys=True)
        + "\n"
    )

    assert plan_declared, (
        "model did not declare a plan via the plan tool\n" + failure_diag
    )

    assert scheduled, (
        "expected pae.idle_tick.scheduled event on plan declare with "
        "continue_plan_autonomously=true under PAE-enabled config. "
        "Suppressed events (for diagnostic):\n"
        + json.dumps(
            [
                {
                    "payload": json.loads(str(e["payload_json"] or "{}")),
                }
                for e in suppressed
            ],
            indent=2,
        )
        + "\n"
        + failure_diag
    )

    assert plan_completed or plan_abandoned, (
        "model did not reach a terminal plan state (complete/abandon). "
        "PAE cancellation requires a terminal plan event.\n" + failure_diag
    )
    assert cancelled, (
        "expected pae.idle_tick.cancelled event after plan terminal "
        "(completed/abandoned).\n" + failure_diag
    )

    remaining_jobs = _cron_jobs(state_db)
    assert not remaining_jobs, (
        "expected zero remaining agentIdleTick cron jobs after "
        "cancellation; residual rows indicate cancel-path store write "
        "did not complete.\n"
        f"remaining_jobs={remaining_jobs!r}\n" + failure_diag
    )
