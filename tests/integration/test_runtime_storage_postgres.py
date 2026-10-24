from __future__ import annotations

import concurrent.futures
import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote_plus
import uuid
from unittest.mock import patch

import pytest

from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    MigrationError,
    migrate_record_store,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.task.run import (
    append_run_state_event,
    list_session_run_events,
    list_session_runs,
    resolve_invocation_terminal,
    resolve_thread_lifecycle,
)
from tests._room_participant_scenarios import (
    COUNTED_JOIN_SEQ_SQL,
    LEGACY_ORDER,
    PRE_UPGRADE_ORDER_SQL,
    check_concurrent_joins,
    check_explicit_active_agent,
    check_join_place_on_upsert_and_rejoin,
    check_leave_and_rejoin_wait_for_each_other,
    check_joins_write_no_session_value,
    check_legacy_owner_join_order,
    check_tied_join_order,
    check_upgraded_join_order,
    legacy_participant_rows,
)
from tests._run_status_scenarios import (
    append_nonstandard_values,
    append_payload_variants,
    append_runs,
    append_thread_payload_variants,
    append_threads,
    check_bounded_reads,
    check_event_facts,
    check_fact_writes,
    check_long_ids,
    check_nonstandard_probe_cases,
    check_nonstandard_values,
    check_review_cases,
    check_token_text_ids,
    check_run_readers,
    check_thread_payload_variants,
    check_thread_projection,
    check_upgraded_history,
    legacy_event_rows,
    recorded_backfill_migrations,
    stepping_clock,
    stored_events,
)

pytestmark = pytest.mark.postgres


def _schema_url(base_url: str, schema_name: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={quote_plus(f'-csearch_path={schema_name}')}"


@pytest.mark.postgres
def test_build_runtime_storage_supports_postgres_backend(tmp_path: Path) -> None:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"ssew_runtime_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

    runtime_storage = build_runtime_storage(
        tmp_path / "state" / "openminion.db",
        env={},
        record_backend="record.postgres",
        record_backend_options={"url": _schema_url(postgres_url, schema_name)},
    )
    try:
        assert isinstance(runtime_storage.sessions, SessionStore)
        assert isinstance(runtime_storage.idempotency, IdempotencyStore)
        assert runtime_storage.migration_result.current_version >= 1

        session = runtime_storage.sessions.resolve_session(
            agent_id="hello-agent",
            channel="console",
            target="postgres-runtime",
        )
        runtime_storage.sessions.append_message(
            session_id=session.id,
            role="inbound",
            body="hello",
            metadata={"source": "postgres"},
        )
        messages = runtime_storage.sessions.list_recent_messages(
            session_id=session.id,
            limit=5,
        )
        reserved = runtime_storage.idempotency.reserve(
            method="turn.send",
            idempotency_key="postgres-key",
            request_hash="h1",
        )
        record = runtime_storage.idempotency.get(
            method="turn.send",
            idempotency_key="postgres-key",
        )

        assert messages
        assert messages[0].body == "hello"
        assert reserved is True
        assert record is not None
        assert record.status == "in_progress"

        fixed_time = "2026-08-11T00:00:00+00:00"
        with patch(
            "openminion.modules.storage.runtime.session_store.lifecycle.utc_now_iso",
            return_value=fixed_time,
        ):
            first_event = runtime_storage.sessions.append_event(
                session_id=session.id,
                event_type="run.queued",
                payload={
                    "run_id": "run-first",
                    "state": "queued",
                    "thread_id": "thread-first",
                    "invocation_id": "invocation-first",
                },
            )
            second_event = runtime_storage.sessions.append_event(
                session_id=session.id,
                event_type="run.queued",
                payload={"run_id": "run-second", "state": "queued"},
            )
        assert first_event.id != second_event.id
        assert first_event.payload["run_id"] == "run-first"
        assert second_event.payload["run_id"] == "run-second"

        runtime_storage.record_store.insert_many(
            "events",
            [
                {
                    "session_id": session.id,
                    "event_type": "noise",
                    "payload_json": json.dumps(
                        {"index": index, "thread_id": "thread-first"}
                    ),
                    "created_at": fixed_time,
                }
                for index in range(2001)
            ],
        )
        projection = resolve_thread_lifecycle(
            runtime_storage.sessions,
            session_id=session.id,
            thread_id="thread-first",
        )
        assert projection.invocation_id == "invocation-first"
        assert projection.invocation_source_event_id == first_event.id

        runtime_storage.sessions.append_event(
            session_id=session.id,
            event_type="response.persisted",
            payload={"run_id": "run-first", "thread_id": "thread-first"},
        )
        runtime_storage.sessions.append_event(
            session_id=session.id,
            event_type="response.delivered",
            payload={"run_id": "run-first", "thread_id": "thread-first"},
        )
        terminal_source = runtime_storage.sessions.append_event(
            session_id=session.id,
            event_type="run.completed",
            payload={
                "run_id": "run-first",
                "state": "completed",
                "thread_id": "thread-first",
            },
        )
        terminal = resolve_invocation_terminal(
            runtime_storage.sessions,
            session_id=session.id,
            trigger_event=terminal_source,
            thread_id="thread-first",
        )
        assert terminal is not None
        assert terminal.invocation_id == "invocation-first"
        assert terminal.resolved_state == "settled"
        assert terminal.source_event_id == terminal_source.id

        before_page = runtime_storage.sessions.list_events_before_id(
            session_id=session.id,
            before_id=second_event.id + 1,
            limit=2,
        )
        assert [event.id for event in before_page] == [
            second_event.id,
            first_event.id,
        ]

        peer_storages = [
            build_runtime_storage(
                tmp_path / f"state/peer-{index}.db",
                env={},
                record_backend="record.postgres",
                record_backend_options={"url": _schema_url(postgres_url, schema_name)},
            )
            for index in range(4)
        ]
        try:
            with patch(
                "openminion.modules.storage.runtime.session_store.lifecycle.utc_now_iso",
                return_value=fixed_time,
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    concurrent_rows = list(
                        pool.map(
                            lambda pair: pair[1].sessions.append_event(
                                session_id=session.id,
                                event_type="run.queued",
                                payload={"run_id": f"parallel-{pair[0]}"},
                            ),
                            enumerate(peer_storages),
                        )
                    )
            assert len({event.id for event in concurrent_rows}) == 4
            assert {event.payload["run_id"] for event in concurrent_rows} == {
                f"parallel-{index}" for index in range(4)
            }
        finally:
            for peer_storage in peer_storages:
                peer_storage.close()
    finally:
        try:
            runtime_storage.close()
        finally:
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()


MESSAGE_CLOCK = "openminion.modules.storage.runtime.session_store.messages.utc_now_iso"
MESSAGE_UUID = "openminion.modules.storage.runtime.session_store.messages.uuid4"
TIED_AT = "2026-10-22T01:00:00+00:00"


@contextmanager
def _postgres_schema(prefix: str) -> Iterator[str]:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"{prefix}_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        yield _schema_url(postgres_url, schema_name)
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        admin_engine.dispose()


@pytest.mark.postgres
def test_postgres_messages_keep_append_order_on_tied_timestamps(
    tmp_path: Path,
) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    with _postgres_schema("om353_order") as url:
        storage = build_runtime_storage(
            tmp_path / "state" / "openminion.db",
            env={},
            record_backend="record.postgres",
            record_backend_options={"url": url},
        )
        try:
            sessions = storage.sessions
            session = sessions.resolve_session(
                agent_id="main", channel="console", target="tied"
            )
            specs = (
                ("inbound", "conv-a", "thread-a"),
                ("outbound", "conv-a", "thread-a"),
                ("inbound", "conv-b", "thread-b"),
                ("outbound", "conv-b", "thread-b"),
            )
            with (
                patch(MESSAGE_CLOCK, return_value=TIED_AT),
                patch(
                    MESSAGE_UUID,
                    side_effect=[SimpleNamespace(hex=c * 32) for c in "fc80"],
                ),
            ):
                appended = [
                    sessions.append_message(
                        session_id=session.id,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        role=role,
                        body=f"{conversation_id}/{role}",
                    )
                    for role, conversation_id, thread_id in specs
                ]
            ids = [item.id for item in appended]

            listed = sessions.list_messages(session_id=session.id)
            assert [item.id for item in listed] == ids
            assert [item.rowid for item in appended] == [1, 2, 3, 4]
            assert {item.created_at for item in listed} == {TIED_AT}
            assert [
                item.id
                for item in sessions.list_recent_messages(
                    session_id=session.id, limit=1
                )
            ] == ids[3:]
            assert [
                item.id
                for item in sessions.list_recent_messages(
                    session_id=session.id, conversation_id="conv-a", limit=1
                )
            ] == ids[1:2]
            assert [
                item.id
                for item in sessions.list_messages(
                    session_id=session.id,
                    conversation_id="conv-b",
                    thread_id="thread-b",
                )
            ] == ids[2:]
            assert sessions.latest_conversation_id(session_id=session.id) == "conv-b"
            assert [
                item.id
                for item in sessions.list_messages_after_rowid(
                    session_id=session.id, after_rowid=appended[0].rowid
                )
            ] == ids[1:]

            # A failed append rolls back the session write and its append_seq.
            before = sessions.get_session(session.id)
            with (
                patch(MESSAGE_CLOCK, return_value="2026-10-22T01:00:01+00:00"),
                patch(MESSAGE_UUID, return_value=SimpleNamespace(hex=ids[0])),
            ):
                with pytest.raises(sqlalchemy.exc.IntegrityError):
                    sessions.append_message(
                        session_id=session.id, role="inbound", body="duplicate id"
                    )
            after = sessions.get_session(session.id)
            assert before is not None and after is not None
            assert (after.updated_at, after.last_activity_at) == (
                before.updated_at,
                before.last_activity_at,
            )
            with patch(MESSAGE_CLOCK, return_value=TIED_AT):
                fifth = sessions.append_message(
                    session_id=session.id, role="inbound", body="fifth"
                )
            assert fifth.rowid == 5
            seq_rows = storage.record_store.query_dicts(
                "SELECT id, append_seq FROM messages WHERE session_id = ?",
                (session.id,),
            )
            assert sorted(int(row["append_seq"]) for row in seq_rows) == [1, 2, 3, 4, 5]

            # Concurrent appends to one session allocate one order.
            peers = [
                build_runtime_storage(
                    tmp_path / f"state/peer-{index}.db",
                    env={},
                    record_backend="record.postgres",
                    record_backend_options={"url": url},
                )
                for index in range(4)
            ]
            barrier = threading.Barrier(len(peers))

            def _append_ten(peer: Any) -> list[Any]:
                barrier.wait()
                return [
                    peer.sessions.append_message(
                        session_id=session.id, role="inbound", body=f"peer-{step}"
                    )
                    for step in range(10)
                ]

            try:
                with patch(MESSAGE_CLOCK, return_value=TIED_AT):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                        per_peer = list(pool.map(_append_ten, peers))
            finally:
                for peer in peers:
                    peer.close()
            records = [item for items in per_peer for item in items]
            assert sorted(item.rowid for item in records) == list(range(6, 46))
            for items in per_peer:
                assert [item.rowid for item in items] == sorted(
                    item.rowid for item in items
                )
            assert [
                item.id for item in sessions.list_messages(session_id=session.id)
            ] == [*ids, fifth.id] + [
                item.id for item in sorted(records, key=lambda item: item.rowid)
            ]
        finally:
            storage.close()


@pytest.mark.postgres
def test_postgres_append_seq_migration_uses_fallback_order_and_moves_tied_boundary(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    from openminion.modules.storage.backends.postgres import RecordStorePostgres

    with _postgres_schema("om353_upgrade") as url:
        record_store = RecordStorePostgres(url)
        try:
            migrate_record_store(
                record_store,
                backend_type="postgres",
                migrations=tuple(
                    item for item in DEFAULT_MIGRATIONS if item.version < 12
                ),
            )
            record_store.execute_count(
                "INSERT INTO sessions(id, session_key, channel, target) "
                "VALUES ('s', 'key-s', 'console', 's')"
            )
            # Append order; g1-g4 share created_at with ids in reverse order.
            for message_id, body, created_at in (
                ("5" * 32, "e0", "2026-10-22T00:59:00+00:00"),
                ("f" * 32, "g1", TIED_AT),
                ("c" * 32, "g2", TIED_AT),
                ("8" * 32, "g3", TIED_AT),
                ("0" * 32, "g4", TIED_AT),
                ("4" * 32, "l1", "2026-10-22T01:01:00+00:00"),
            ):
                record_store.execute_count(
                    "INSERT INTO messages(id, session_id, role, body, created_at) "
                    "VALUES (?, 's', 'inbound', ?, ?)",
                    (message_id, body, created_at),
                )
            record_store.execute_count(
                "INSERT INTO session_contexts(session_id, rolling_summary, "
                "compacted_until_rowid, compacted_until_created_at, "
                "compacted_until_message_id, compacted_message_count) "
                "VALUES ('s', 'summary of e0, g3', 2, ?, ?, 2)",
                (TIED_AT, "8" * 32),
            )

            result = migrate_record_store(record_store, backend_type="postgres")
            assert result.applied_versions == (12, 13, 14)

            seq_rows = record_store.query_dicts(
                "SELECT body, append_seq FROM messages ORDER BY append_seq"
            )
            # Postgres stored no insertion order: the backfill keeps the
            # pre-upgrade (created_at, id) read order.
            assert [(row["body"], int(row["append_seq"])) for row in seq_rows] == [
                ("e0", 1),
                ("g4", 2),
                ("g3", 3),
                ("g2", 4),
                ("g1", 5),
                ("l1", 6),
            ]
            store = SessionStore(record_store)
            context = store.get_session_context(session_id="s")
            assert context is not None
            assert (
                context.compacted_until_rowid,
                context.compacted_until_created_at,
                context.compacted_until_message_id,
                context.compacted_message_count,
                context.rolling_summary,
            ) == (1, "2026-10-22T00:59:00+00:00", "5" * 32, 1, "summary of e0, g3")
            assert [
                item.body
                for item in store.list_messages_after_rowid(
                    session_id="s", after_rowid=context.compacted_until_rowid
                )
            ] == ["g4", "g3", "g2", "g1", "l1"]
            with patch(MESSAGE_CLOCK, return_value=TIED_AT):
                appended = store.append_message(
                    session_id="s", role="inbound", body="after-upgrade"
                )
            assert appended.rowid == 6
            assert [item.body for item in store.list_messages(session_id="s")] == [
                "e0",
                "g4",
                "g3",
                "g2",
                "g1",
                "after-upgrade",
                "l1",
            ]
        finally:
            record_store.close()


@pytest.mark.postgres
def test_postgres_run_status_readers_match_the_event_reduction(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_runs") as url:
        storage = build_runtime_storage(
            tmp_path / "state" / "openminion.db",
            env={},
            record_backend="record.postgres",
            record_backend_options={"url": url},
        )
        try:
            sessions = storage.sessions
            runs, threads = (
                sessions.resolve_session(agent_id="main", channel="console", target=t)
                for t in ("runs", "threads")
            )
            with stepping_clock():
                append_runs(sessions, runs.id)
                append_threads(sessions, threads.id)

            check_run_readers(sessions, runs.id)
            check_thread_projection(sessions, threads.id)
        finally:
            storage.close()


@pytest.mark.postgres
def test_postgres_run_status_normalizes_payload_values(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_payloads") as url:
        storage = build_runtime_storage(
            tmp_path / "state" / "openminion.db",
            env={},
            record_backend="record.postgres",
            record_backend_options={"url": url},
        )
        try:
            sessions = storage.sessions

            def new_session(target: str) -> str:
                return sessions.resolve_session(
                    agent_id="main", channel="console", target=target
                ).id

            runs, threads = new_session("runs"), new_session("threads")
            append_payload_variants(sessions, runs)
            append_thread_payload_variants(sessions, threads)

            check_run_readers(sessions, runs)
            check_thread_payload_variants(sessions, threads)
            check_review_cases(sessions, new_session)
            check_token_text_ids(sessions, new_session)
        finally:
            storage.close()


@contextmanager
def _legacy_postgres_store(url: str) -> Iterator[Any]:
    """A Postgres record store at runtime migration 12 (before the event facts)."""
    from openminion.modules.storage.backends.postgres import RecordStorePostgres

    record_store = RecordStorePostgres(url)
    try:
        migrate_record_store(
            record_store,
            backend_type="postgres",
            migrations=tuple(item for item in DEFAULT_MIGRATIONS if item.version < 13),
        )
        yield record_store
    finally:
        record_store.close()


@contextmanager
def _postgres_sessions(tmp_path: Path, url: str) -> Iterator[SessionStore]:
    storage = build_runtime_storage(
        tmp_path / "state" / "openminion.db",
        env={},
        record_backend="record.postgres",
        record_backend_options={"url": url},
    )
    try:
        yield storage.sessions
    finally:
        storage.close()


def _session_factory(sessions: SessionStore) -> Callable[[str], str]:
    def new_session(target: str) -> str:
        return sessions.resolve_session(
            agent_id="main", channel="console", target=target
        ).id

    return new_session


@pytest.mark.postgres
def test_postgres_run_status_readers_past_the_default_window(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_window") as url:
        thread = {"conversation_id": "conv", "thread_id": "thread"}
        # 1,999 events of an older run, stored before runtime migration 13, so
        # its first event is below the default window of 2,000 run events once
        # the new run starts. The migration backfills their facts.
        with _legacy_postgres_store(url) as record_store:
            record_store.execute_count(
                "INSERT INTO sessions(id, session_key, channel, target) "
                "VALUES ('window', 'key-window', 'console', 'window')"
            )
            record_store.insert_many(
                "events",
                [
                    {
                        "session_id": "window",
                        "event_type": f"run.{state}",
                        "payload_json": json.dumps(
                            {"run_id": "old", "state": state, "step": "s", **thread}
                        ),
                        "created_at": f"2026-10-23T09:{index // 60:02d}:"
                        f"{index % 60:02d}.000001+00:00",
                    }
                    for index, state in enumerate(
                        ["queued"] + ["running"] * 1997 + ["completed"]
                    )
                ],
            )
        with _postgres_sessions(tmp_path, url) as sessions:
            appended = [
                append_run_state_event(
                    sessions,
                    session_id="window",
                    run_id="new",
                    state=state,
                    current_step="step",
                    payload={"invocation_id": "new-inv"},
                    **thread,
                )
                for state in ("queued", "failed")
            ]
            runs = list_session_runs(sessions, session_id="window")
            new_events = list_session_run_events(
                sessions, session_id="window", run_id="new"
            )
            projection = resolve_thread_lifecycle(
                sessions, session_id="window", **thread
            )

            assert [(run.run_id, run.state, run.event_count) for run in runs] == [
                ("new", "failed", 2),
                ("old", "completed", 1999),
            ]
            assert runs[1].started_at == "2026-10-23T09:00:00.000001+00:00"
            assert [event.id for event in new_events] == [e.id for e in appended]
            assert (projection.latest_run_id, projection.invocation_id) == (
                "new",
                "new-inv",
            )
            assert projection.latest_event_id == sessions.event_high_water(
                session_id="window"
            )


@pytest.mark.postgres
def test_postgres_event_facts_read_payload_values_as_the_run_status_readers(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_facts") as url:
        with _postgres_sessions(tmp_path, url) as sessions:
            check_event_facts(sessions, _session_factory(sessions)("facts"))


@pytest.mark.postgres
def test_postgres_event_facts_follow_the_event_transaction(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_fact_writes") as url:
        with _postgres_sessions(tmp_path, url) as sessions:
            check_fact_writes(sessions, _session_factory(sessions))


@pytest.mark.postgres
def test_postgres_run_status_reads_nonstandard_payload_values(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_nonstandard") as url:
        with _postgres_sessions(tmp_path, url) as sessions:
            new_session = _session_factory(sessions)
            session_id = new_session("nonstandard")
            append_nonstandard_values(sessions, session_id)
            check_nonstandard_values(sessions, session_id, nul_requests=False)
            check_nonstandard_probe_cases(sessions, new_session)


@pytest.mark.postgres
def test_postgres_run_status_accepts_ids_of_any_length(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_long_ids") as url:
        with _postgres_sessions(tmp_path, url) as sessions:
            check_long_ids(sessions, _session_factory(sessions))


@pytest.mark.postgres
def test_postgres_run_status_reads_a_bounded_number_of_rows(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_bounds") as url:
        with _postgres_sessions(tmp_path, url) as sessions:
            check_bounded_reads(sessions, _session_factory(sessions)("bounds"))


def _insert_legacy_history(record_store: Any, count: int) -> list[str]:
    session_ids = ["legacy-a", "legacy-b"]
    for session_id in session_ids:
        record_store.execute_count(
            "INSERT INTO sessions(id, session_key, channel, target) "
            "VALUES (?, ?, 'console', ?)",
            (session_id, f"key-{session_id}", session_id),
        )
    record_store.insert_many("events", legacy_event_rows(session_ids, count))
    return session_ids


@pytest.mark.postgres
def test_postgres_event_facts_migration_backfills_existing_history(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_backfill") as url:
        # 2,345 events: three backfill batches of up to 1,000.
        with _legacy_postgres_store(url) as record_store:
            session_ids = _insert_legacy_history(record_store, 2345)
            stored = stored_events(SessionStore(record_store))
            batches: list[int] = []
            result = migrate_record_store(
                record_store,
                backend_type="postgres",
                migrations=recorded_backfill_migrations(batches),
            )
            assert result.applied_versions == (13, 14)
            assert batches == [1000, 1000, 345, 0]
        with _postgres_sessions(tmp_path, url) as sessions:
            check_upgraded_history(
                sessions,
                _session_factory(sessions),
                session_ids=session_ids,
                stored=stored,
            )


@pytest.mark.postgres
def test_postgres_event_facts_migration_rolls_back_when_the_backfill_fails(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om352_backfill_fail") as url:
        with _legacy_postgres_store(url) as record_store:
            _insert_legacy_history(record_store, 1500)
            # A payload `json.loads` cannot read, after the first batch of 1,000.
            record_store.execute_count(
                "UPDATE events SET payload_json = ? WHERE id = 1400", ('{"run_id": ',)
            )
            stored = stored_events(SessionStore(record_store))

            with pytest.raises(
                MigrationError,
                match="add_event_run_facts.*event 1400 has a payload_json that is not",
            ):
                migrate_record_store(record_store, backend_type="postgres")

            assert record_store.query_dicts(
                "SELECT MAX(version) AS version FROM migrations"
            ) == [{"version": 12}]
            assert record_store.query_dicts(
                "SELECT to_regclass('event_run_facts') IS NULL AS missing"
            ) == [{"missing": True}]
            assert stored_events(SessionStore(record_store)) == stored

            # Rerunning the migration after the fix applies it whole.
            record_store.execute_count(
                "UPDATE events SET payload_json = '{}' WHERE id = 1400"
            )
            result = migrate_record_store(record_store, backend_type="postgres")
            assert result.applied_versions == (13, 14)
            assert record_store.query_dicts(
                "SELECT COUNT(*) AS facts FROM event_run_facts"
            ) == [{"facts": 1500}]


@pytest.mark.postgres
@pytest.mark.parametrize(
    "check",
    [
        check_tied_join_order,
        check_join_place_on_upsert_and_rejoin,
        check_explicit_active_agent,
        check_legacy_owner_join_order,
        check_joins_write_no_session_value,
    ],
)
def test_postgres_participants_keep_join_order_on_tied_timestamps(
    tmp_path: Path, check: Callable[[SessionStore], None]
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om364_join_order") as url:
        with _postgres_sessions(tmp_path, url) as sessions:
            check(sessions)


@pytest.mark.postgres
def test_postgres_concurrent_joins_allocate_distinct_join_order(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om364_concurrent") as url:
        peers = [
            build_runtime_storage(
                tmp_path / f"state/peer-{index}.db",
                env={},
                record_backend="record.postgres",
                record_backend_options={"url": url},
            )
            for index in range(4)
        ]
        try:
            with _postgres_sessions(tmp_path, url) as sessions:
                check_concurrent_joins(sessions, [peer.sessions for peer in peers])
        finally:
            for peer in peers:
                peer.close()


@pytest.mark.postgres
def test_postgres_leave_and_rejoin_on_two_connections_wait_for_each_other(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    with _postgres_schema("om364_leave_rejoin") as url:
        leaver, joiner = (
            build_runtime_storage(
                tmp_path / f"state/{name}.db",
                env={},
                record_backend="record.postgres",
                record_backend_options={"url": url},
            )
            for name in ("leaver", "joiner")
        )
        try:
            with _postgres_sessions(tmp_path, url) as sessions:
                check_leave_and_rejoin_wait_for_each_other(
                    sessions, leaver.sessions, joiner.sessions
                )
        finally:
            leaver.close()
            joiner.close()


@pytest.mark.postgres
def test_postgres_join_seq_migration_numbers_participants_in_pre_upgrade_order(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sqlalchemy")
    from openminion.modules.storage.backends.postgres import RecordStorePostgres

    with _postgres_schema("om364_upgrade") as url:
        record_store = RecordStorePostgres(url)
        try:
            migrate_record_store(
                record_store,
                backend_type="postgres",
                migrations=tuple(
                    item for item in DEFAULT_MIGRATIONS if item.version < 14
                ),
            )
            sessions, participants = legacy_participant_rows()
            record_store.insert_many("sessions", sessions)
            record_store.insert_many("room_participants", participants)
            for session_id, order in LEGACY_ORDER.items():
                assert [
                    row["participant_id"]
                    for row in record_store.query_dicts(
                        PRE_UPGRADE_ORDER_SQL, (session_id,)
                    )
                ] == order
            counted = {
                str(row["id"]): int(row["join_seq"])
                for row in record_store.query_dicts(COUNTED_JOIN_SEQ_SQL)
            }

            result = migrate_record_store(record_store, backend_type="postgres")
            assert result.applied_versions == (14,)
            assert record_store.query_dicts(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND tablename = 'room_participants' "
                "AND indexdef LIKE 'CREATE UNIQUE INDEX%(session_id, join_seq)'"
            ) == [{"indexname": "idx_room_participants_session_join_seq"}]
            check_upgraded_join_order(SessionStore(record_store), counted)
        finally:
            record_store.close()
