from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
from urllib.parse import quote_plus
import uuid
from unittest.mock import patch

import pytest

from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.task.run import (
    resolve_invocation_terminal,
    resolve_thread_lifecycle,
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
