import itertools
from datetime import datetime, timedelta, timezone

import pytest

from openminion.services.runtime.run_status import (
    RUN_STATE_CANCELLED,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_QUEUED,
    RUN_STATE_RESPONDING,
    RUN_STATE_RUNNING,
    RUN_STATE_WAITING_TOOL,
    THREAD_STATE_FAILED,
    append_run_state_event,
    list_session_run_events,
    list_session_runs,
    resolve_thread_lifecycle,
)
from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    migrate_database,
)
from openminion.modules.storage.runtime.session_store import (
    SessionStore,
    lifecycle,
    run_facts,
)
from openminion.modules.storage.runtime.session_store.rows import metadata_json
from openminion.modules.storage.runtime.sqlite import connect_database
from tests._run_status_scenarios import (
    append_nonstandard_values,
    append_payload_variants,
    append_runs,
    check_bounded_reads,
    check_long_ids,
    check_nonstandard_probe_cases,
    check_nonstandard_values,
    check_review_cases,
    check_run_readers,
    check_token_text_ids,
    stepping_clock,
)


def test_service_run_status_surface_is_canonical_task_owner() -> None:
    from openminion.modules.task.run import Run as canonical
    from openminion.services.runtime.run_status import Run as compatibility

    assert compatibility is canonical


def test_run_states_are_the_states_the_store_normalizes_to() -> None:
    assert run_facts.RUN_STATES == {
        RUN_STATE_QUEUED,
        RUN_STATE_RUNNING,
        RUN_STATE_WAITING_TOOL,
        RUN_STATE_RESPONDING,
        RUN_STATE_COMPLETED,
        RUN_STATE_FAILED,
        RUN_STATE_CANCELLED,
    }
    assert run_facts.DEFAULT_RUN_STATE == RUN_STATE_RUNNING


def _build_sessions(tmp_path):
    database_path = tmp_path / "state" / "openminion.db"
    migrate_database(database_path)
    connection = connect_database(database_path)
    sessions = SessionStore(connection)
    session = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target="run-status",
    )
    return connection, sessions, session


def test_list_session_runs_returns_latest_state_and_terminal_time(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        run_id = "run-1"
        for state, step in (
            (RUN_STATE_QUEUED, "turn.accepted"),
            (RUN_STATE_RUNNING, "agent.generate"),
            (RUN_STATE_RESPONDING, "channel.send"),
            (RUN_STATE_COMPLETED, "turn.completed"),
        ):
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id=run_id,
                state=state,
                current_step=step,
            )

        runs = list_session_runs(sessions, session_id=session.id, limit=10)
    finally:
        connection.close()

    assert len(runs) == 1
    summary = runs[0]
    assert summary.run_id == run_id
    assert summary.state == RUN_STATE_COMPLETED
    assert summary.current_step == "turn.completed"
    assert summary.event_count == 4
    assert summary.started_at
    assert summary.ended_at


def test_list_session_run_events_filters_run_id(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        append_run_state_event(
            sessions,
            session_id=session.id,
            run_id="run-a",
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
        )
        append_run_state_event(
            sessions,
            session_id=session.id,
            run_id="run-b",
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
        )
        append_run_state_event(
            sessions,
            session_id=session.id,
            run_id="run-a",
            state=RUN_STATE_RUNNING,
            current_step="agent.generate",
        )

        events = list_session_run_events(
            sessions,
            session_id=session.id,
            run_id="run-a",
            limit=10,
        )
    finally:
        connection.close()

    assert len(events) == 2
    assert [event.run_id for event in events] == ["run-a", "run-a"]
    assert [event.state for event in events] == [RUN_STATE_QUEUED, RUN_STATE_RUNNING]


def test_list_session_run_events_limit_returns_latest_n_events(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        run_id = "run-limit"
        for index in range(5):
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id=run_id,
                state=RUN_STATE_RUNNING,
                current_step=f"step-{index}",
                payload={"index": index},
            )

        events = list_session_run_events(
            sessions,
            session_id=session.id,
            run_id=run_id,
            limit=2,
        )
    finally:
        connection.close()

    assert len(events) == 2
    assert events[0].current_step == "step-3"
    assert events[1].current_step == "step-4"


def test_run_state_follows_insertion_order_when_clock_steps_back(
    tmp_path, monkeypatch
) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    # The wall clock steps back ~0.85 s between running and failed.
    queued_at = "2026-10-21T17:16:59.564721+00:00"
    failed_at = "2026-10-21T17:16:58.715677+00:00"
    stamps = iter([queued_at, "2026-10-21T17:16:59.602657+00:00", failed_at])
    monkeypatch.setattr(lifecycle, "utc_now_iso", lambda: next(stamps))
    try:
        appended = [
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id="run-1",
                state=state,
                current_step=step,
            )
            for state, step in (
                (RUN_STATE_QUEUED, "turn.accepted"),
                (RUN_STATE_RUNNING, "agent.generate"),
                (RUN_STATE_FAILED, "turn.failed"),
            )
        ]
        runs = list_session_runs(sessions, session_id=session.id, limit=10)
        events = list_session_run_events(
            sessions, session_id=session.id, run_id="run-1"
        )
        latest = list_session_run_events(
            sessions, session_id=session.id, run_id="run-1", limit=1
        )
    finally:
        connection.close()

    assert [event.id for event in events] == [event.id for event in appended]
    assert [event.state for event in events] == [
        RUN_STATE_QUEUED,
        RUN_STATE_RUNNING,
        RUN_STATE_FAILED,
    ]
    assert [event.current_step for event in latest] == ["turn.failed"]
    assert len(runs) == 1
    summary = runs[0]
    assert summary.state == RUN_STATE_FAILED
    assert summary.current_step == "turn.failed"
    assert summary.event_count == 3
    assert summary.started_at == queued_at
    assert summary.ended_at == failed_at


def _forward_clock():
    base = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
    return (
        (base + timedelta(milliseconds=tick)).isoformat() for tick in itertools.count(1)
    )


def _append(sessions, session, run_id, state, step="", **payload):
    return append_run_state_event(
        sessions,
        session_id=session.id,
        run_id=run_id,
        state=state,
        current_step=step,
        payload=payload or None,
    )


def test_readers_report_the_newest_run_past_the_default_window(tmp_path) -> None:
    # The probe from #352: 2,000 events of an older completed run, then a new
    # invocation's queued and failed events, with the real clock.
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        appended = [
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id="old" if index < 2000 else "new",
                state=(
                    RUN_STATE_QUEUED
                    if index % 2 == 0
                    else (RUN_STATE_COMPLETED if index < 2000 else RUN_STATE_FAILED)
                ),
                current_step="step",
                conversation_id="conv",
                thread_id="thread",
                payload={"invocation_id": "old-inv" if index < 2000 else "new-inv"},
            )
            for index in range(2002)
        ]
        runs = list_session_runs(sessions, session_id=session.id)
        new_events = list_session_run_events(
            sessions, session_id=session.id, run_id="new"
        )
        old_latest = list_session_run_events(
            sessions, session_id=session.id, run_id="old", limit=3
        )
        projection = resolve_thread_lifecycle(
            sessions, session_id=session.id, conversation_id="conv", thread_id="thread"
        )
        high_water = sessions.event_high_water(session_id=session.id)
    finally:
        connection.close()

    old_first, old_last = appended[0], appended[1999]
    new_queued, new_failed = appended[2000], appended[2001]
    assert [(run.run_id, run.state, run.event_count) for run in runs] == [
        ("new", RUN_STATE_FAILED, 2),
        ("old", RUN_STATE_COMPLETED, 2000),
    ]
    assert (runs[0].started_at, runs[0].ended_at) == (
        new_queued.created_at,
        new_failed.created_at,
    )
    assert (runs[1].started_at, runs[1].ended_at) == (
        old_first.created_at,
        old_last.created_at,
    )
    assert [event.id for event in new_events] == [new_queued.id, new_failed.id]
    assert [event.id for event in old_latest] == [
        event.id for event in appended[1997:2000]
    ]
    assert (projection.latest_run_id, projection.latest_run_state) == (
        "new",
        RUN_STATE_FAILED,
    )
    assert projection.thread_state == THREAD_STATE_FAILED
    assert (projection.invocation_id, projection.invocation_source_event_id) == (
        "new-inv",
        new_queued.id,
    )
    assert projection.latest_event_id == high_water == new_failed.id
    assert projection.updated_at == new_failed.created_at


@pytest.mark.parametrize("old_events", [1998, 1999])
def test_default_window_boundary_keeps_both_runs_whole(
    tmp_path, monkeypatch, old_events
) -> None:
    # 2,000 run events fill the default window exactly; with 2,001 the old run's
    # first event falls below it. The probe above is the 2,002 case. The old
    # run was stored before runtime migration 13, which backfills its facts.
    database_path = tmp_path / "state" / "openminion.db"
    migrate_database(
        database_path,
        migrations=tuple(item for item in DEFAULT_MIGRATIONS if item.version < 13),
    )
    connection = connect_database(database_path)
    sessions = SessionStore(connection)
    session = sessions.resolve_session(
        agent_id="main", channel="console", target="run-status"
    )
    stamps = _forward_clock()
    thread = {"conversation_id": "conv", "thread_id": "thread"}
    old_states = (
        [RUN_STATE_QUEUED]
        + [RUN_STATE_RUNNING] * (old_events - 2)
        + [RUN_STATE_COMPLETED]
    )
    try:
        # The old run's rows as `append_run_state_event` stores them, in bulk.
        connection.executemany(
            "INSERT INTO events(session_id, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            [
                (
                    session.id,
                    f"run.{state}",
                    metadata_json(
                        {
                            "run_id": "old",
                            "state": state,
                            "step": "step",
                            "invocation_id": "old-inv",
                            **thread,
                        }
                    ),
                    next(stamps),
                )
                for state in old_states
            ],
        )
        connection.commit()
        assert migrate_database(database_path).applied_versions == (13, 14)
        monkeypatch.setattr(lifecycle, "utc_now_iso", lambda: next(stamps))
        new_queued, new_failed = (
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id="new",
                state=state,
                current_step="step",
                payload={"invocation_id": "new-inv"},
                **thread,
            )
            for state in (RUN_STATE_QUEUED, RUN_STATE_FAILED)
        )
        runs = list_session_runs(sessions, session_id=session.id)
        old_read = list_session_run_events(
            sessions, session_id=session.id, run_id="old", limit=1000
        )
        projection = resolve_thread_lifecycle(sessions, session_id=session.id, **thread)
        high_water = sessions.event_high_water(session_id=session.id)
        old_first = sessions.list_events(session_id=session.id, limit=1)[0]
    finally:
        connection.close()

    assert high_water == old_events + 2 == new_failed.id
    assert [(run.run_id, run.state, run.event_count) for run in runs] == [
        ("new", RUN_STATE_FAILED, 2),
        ("old", RUN_STATE_COMPLETED, old_events),
    ]
    assert runs[0].started_at == new_queued.created_at
    assert runs[1].started_at == old_first.created_at
    assert [event.id for event in old_read] == list(
        range(old_events - 999, old_events + 1)
    )
    assert (projection.latest_run_id, projection.invocation_id) == ("new", "new-inv")
    assert projection.latest_event_id == high_water


def test_run_started_below_the_window_reports_all_of_its_events(
    tmp_path, monkeypatch
) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    stamps = _forward_clock()
    monkeypatch.setattr(lifecycle, "utc_now_iso", lambda: next(stamps))
    try:
        appended = [
            _append(sessions, session, run_id, state, step, **payload)
            for run_id, state, step, payload in (
                ("long", RUN_STATE_QUEUED, "turn.accepted", {}),
                ("long", RUN_STATE_FAILED, "turn.failed", {"error": "boom"}),
                ("early", RUN_STATE_QUEUED, "turn.accepted", {}),
                ("early", RUN_STATE_COMPLETED, "turn.completed", {}),
                ("late", RUN_STATE_QUEUED, "turn.accepted", {}),
                ("long", RUN_STATE_RUNNING, "", {}),
                ("late", RUN_STATE_COMPLETED, "turn.completed", {}),
                ("long", RUN_STATE_WAITING_TOOL, "", {}),
            )
        ]
        # The newest 4 run events hold "late" and the last two of "long".
        windowed = list_session_runs(
            sessions, session_id=session.id, limit=2, scan_limit=4
        )
        whole = list_session_runs(sessions, session_id=session.id, limit=10)
        long_latest = list_session_run_events(
            sessions, session_id=session.id, run_id="long", limit=3
        )
    finally:
        connection.close()

    assert [run.run_id for run in windowed] == ["late", "long"]
    long_run = windowed[1]
    assert (
        long_run.state,
        long_run.current_step,
        long_run.event_count,
        long_run.error,
    ) == (RUN_STATE_WAITING_TOOL, "turn.failed", 4, "boom")
    assert (long_run.started_at, long_run.ended_at) == (
        appended[0].created_at,
        appended[1].created_at,
    )
    assert [run.run_id for run in whole] == ["late", "early", "long"]
    assert whole[0] == windowed[0]
    assert whole[2] == long_run
    assert [event.id for event in long_latest] == [
        appended[index].id for index in (1, 5, 7)
    ]


def test_run_order_uses_stored_started_at_when_clock_steps_back_below_window(
    tmp_path, monkeypatch
) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    # "stepped" starts at 10:00:09 and the clock then steps back; with a window
    # of 3 run events, its first event is below the window.
    stamps = [
        "2026-10-23T10:00:09.000000+00:00",
        "2026-10-23T10:00:01.000000+00:00",
        "2026-10-23T10:00:02.000000+00:00",
        "2026-10-23T10:00:05.000000+00:00",
        "2026-10-23T10:00:06.000000+00:00",
    ]
    monkeypatch.setattr(lifecycle, "utc_now_iso", iter(stamps).__next__)
    try:
        for run_id, state in (
            ("stepped", RUN_STATE_QUEUED),
            ("gone", RUN_STATE_QUEUED),
            ("stepped", RUN_STATE_RUNNING),
            ("steady", RUN_STATE_QUEUED),
            ("steady", RUN_STATE_COMPLETED),
        ):
            _append(sessions, session, run_id, state)
        first = list_session_runs(
            sessions, session_id=session.id, limit=1, scan_limit=3
        )
        first_two = list_session_runs(
            sessions, session_id=session.id, limit=2, scan_limit=3
        )
    finally:
        connection.close()

    assert [(run.run_id, run.started_at, run.event_count) for run in first] == [
        ("stepped", stamps[0], 2)
    ]
    assert [(run.run_id, run.started_at) for run in first_two] == [
        ("stepped", stamps[0]),
        ("steady", stamps[3]),
    ]


def test_run_readers_keep_filter_limit_and_id_page_agreement(
    tmp_path, monkeypatch
) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    stamps = _forward_clock()
    monkeypatch.setattr(lifecycle, "utc_now_iso", lambda: next(stamps))
    try:
        r1 = [_append(sessions, session, "r1", RUN_STATE_QUEUED, "accepted")]
        sessions.append_event(
            session_id=session.id, event_type="tool.call", payload={"run_id": "r1"}
        )
        # Run events that name "r1" only in a nested object, or in another case.
        _append(sessions, session, "r2", RUN_STATE_QUEUED, parent={"run_id": "r1"})
        _append(sessions, session, "R1", RUN_STATE_QUEUED)
        r1.append(_append(sessions, session, "r1", RUN_STATE_RUNNING, "generate"))
        r1x = _append(sessions, session, "r1x", RUN_STATE_QUEUED)
        r1.append(_append(sessions, session, "r1", RUN_STATE_RESPONDING, "send"))
        r1.append(_append(sessions, session, "r1", RUN_STATE_COMPLETED, "done"))
        # The newest 3 run events are "r1x" and the last two of "r1".
        first = list_session_runs(
            sessions, session_id=session.id, limit=1, scan_limit=3
        )
        first_two = list_session_runs(
            sessions, session_id=session.id, limit=2, scan_limit=3
        )
        r1_events = list_session_run_events(
            sessions, session_id=session.id, run_id=" r1 ", limit=10
        )
        r1_latest = list_session_run_events(
            sessions, session_id=session.id, run_id="r1", limit=2
        )
        unknown = list_session_run_events(sessions, session_id=session.id, run_id="r9")
        high_water = sessions.event_high_water(session_id=session.id)
        id_page = sessions.list_events_before_id(
            session_id=session.id, before_id=high_water + 1, limit=100
        )
    finally:
        connection.close()

    assert [run.run_id for run in first] == ["r1x"]
    assert [(run.run_id, run.event_count) for run in first_two] == [
        ("r1x", 1),
        ("r1", 4),
    ]
    assert first_two[0].started_at == r1x.created_at
    assert (first_two[1].started_at, first_two[1].ended_at) == (
        r1[0].created_at,
        r1[-1].created_at,
    )
    paged_r1 = [
        event.id
        for event in reversed(id_page)
        if event.event_type.startswith("run.") and event.payload["run_id"] == "r1"
    ]
    assert [event.id for event in r1_events] == paged_r1 == [event.id for event in r1]
    assert [event.id for event in r1_latest] == paged_r1[-2:]
    assert first_two[1].event_count == len(r1_events)
    assert unknown == []


def test_run_readers_match_the_event_reduction_at_every_window(tmp_path) -> None:
    # The clock steps back every fifth append, so out-of-order `created_at`
    # values cross each window boundary the scenario checks.
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        with stepping_clock():
            append_runs(sessions, session.id)
        check_run_readers(sessions, session.id)
    finally:
        connection.close()


def test_run_readers_normalize_payload_values_as_the_python_readers(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        append_payload_variants(sessions, session.id)
        check_run_readers(sessions, session.id)

        def new_session(target: str) -> str:
            return sessions.resolve_session(
                agent_id="main", channel="console", target=target
            ).id

        check_review_cases(sessions, new_session)
        check_token_text_ids(sessions, new_session)
    finally:
        connection.close()


def test_run_readers_read_a_bounded_number_of_rows_for_any_history(
    tmp_path,
) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        check_bounded_reads(sessions, session.id)
    finally:
        connection.close()


def test_run_readers_read_nonstandard_payload_values_as_stored(tmp_path) -> None:
    # NaN, Infinity and NUL in run events and in unrelated tool events of the
    # thread: the readers report what the event reduction reports.
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        append_nonstandard_values(sessions, session.id)
        check_nonstandard_values(sessions, session.id)

        def new_session(target: str) -> str:
            return sessions.resolve_session(
                agent_id="main", channel="console", target=target
            ).id

        check_nonstandard_probe_cases(sessions, new_session)
    finally:
        connection.close()


def test_run_readers_accept_ids_of_any_length(tmp_path) -> None:
    connection, sessions, _session = _build_sessions(tmp_path)
    try:
        check_long_ids(
            sessions,
            lambda target: (
                sessions.resolve_session(
                    agent_id="main", channel="console", target=target
                ).id
            ),
        )
    finally:
        connection.close()
