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


_AGENT_IDS = ("minimax-m2-5", "minimax-m2-7")
_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)

_PROBE_MESSAGE = (
    "I want you to practice cross-turn goal persistence. Use the `plan` "
    'tool with action="declare" to create a plan that has exactly '
    'three steps: step 1 "acknowledge start", step 2 "acknowledge '
    'midpoint", step 3 "acknowledge finish". Set '
    "continue_plan_autonomously to true so the runtime schedules the "
    "follow-up turns automatically. On each subsequent autonomous "
    'turn, use the `plan` tool with action="step_completed" to mark '
    "the next step complete (still setting continue_plan_autonomously=true "
    "for steps 1 and 2). After step 3 is completed, call the `plan` "
    'tool with action="complete". Do not ask me any questions — '
    "work through the plan autonomously."
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


@pytest.mark.e2e
@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_live_minimax_official_ctgp_three_step_autonomous_plan(agent_id: str) -> None:
    require_live_flag()
    if not _OFFICIAL_CONFIG.exists():
        pytest.skip(f"missing config file: {_OFFICIAL_CONFIG}")

    run_id = f"ctgp-auto-{agent_id}-{int(time.time())}"
    data_root = artifact_dir() / "data-roots" / run_id

    result = run_cli_session(
        session_id_prefix=run_id,
        user_input=f"{_PROBE_MESSAGE}\n/exit\n",
        agent_id=agent_id,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=data_root,
        matrix_type="skill_dense",
    )

    state_db = data_root / "state" / "brain" / "sessions.db"
    events = _session_events(
        state_db,
        event_types=(
            "autonomous_turn.fired",
            "task_plan.declared",
            "task_plan.step_completed",
            "task_plan.completed",
            "task_plan.abandoned",
            "brain.autonomous_continuation.stopped",
        ),
    )
    by_type: dict[str, list[dict]] = {}
    for event in events:
        by_type.setdefault(str(event["event_type"]), []).append(event)

    autonomous_fires = by_type.get("autonomous_turn.fired", [])
    plan_declared = by_type.get("task_plan.declared", [])

    failure_diag = (
        f"transcript={result.transcript_path}\n"
        f"state_db={state_db}\n"
        f"event_counts={{k: len(v) for k, v in by_type.items()}}\n"
        + json.dumps({k: len(v) for k, v in by_type.items()}, indent=2, sort_keys=True)
    )

    assert plan_declared, (
        "model did not declare a plan via the plan tool\n" + failure_diag
    )

    # CTGP-06 exit criterion: at least 2 autonomous-turn fires (the
    # user turn plus those autonomous turns totals the ">=3 turns
    # the per-plan cap is 10 (CTGP-04 default).
    assert len(autonomous_fires) >= 2, (
        "expected at least 2 autonomous_turn.fired events (3-step plan "
        "with continue_plan_autonomously=true should drive 2 follow-up "
        "autonomous turns after the user-driven declare)\n"
        f"observed={len(autonomous_fires)}\n" + failure_diag
    )
