from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus
import uuid

import pytest

from openminion.modules.session.runtime.factory import build_module_session_store
from openminion.modules.session.storage.base import SessionStore
from openminion.modules.session.storage.store import PostgresSessionStore
from openminion.modules.storage.engine import StorageEngineConfig
from openminion.modules.storage.backends.postgres import (
    RecordStorePostgres,
)

pytestmark = pytest.mark.postgres


def _schema_url(base_url: str, schema_name: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={quote_plus(f'-csearch_path={schema_name}')}"


def _open_postgres_session_store(tmp_path: Path):
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"ssew_session_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sqlalchemy.create_engine(
        _schema_url(postgres_url, schema_name),
        future=True,
    )
    record_store = RecordStorePostgres(engine)
    store = PostgresSessionStore(tmp_path / "sessions.db", record_store=record_store)
    return sqlalchemy, admin_engine, engine, schema_name, store


@pytest.mark.postgres
def test_postgres_session_store_core_lifecycle_round_trip(tmp_path: Path) -> None:
    sqlalchemy, admin_engine, engine, schema_name, store = _open_postgres_session_store(
        tmp_path
    )
    try:
        session_id = store.create_session(
            initial_agent_id="hello-agent",
            profile_version="v1",
            title="Postgres Session",
            tags=["postgres"],
        )
        turn_id = store.append_turn(
            session_id,
            role="user",
            content="hello from postgres",
            meta={"source": "test"},
        )
        event_id = store.append_event(
            session_id,
            event_type="tool.completed",
            payload={"tool_name": "time", "summary": "UTC time"},
            agent_id="hello-agent",
        )

        session = store.get_session(session_id)
        recent_turns = store.get_recent_turns(session_id, limit_messages=5)
        sessions = store.list_sessions(limit=5)
        store.update_summary(
            session_id,
            "short summary",
            summary_long="long summary",
            based_on_seq=1,
        )
        session_slice = store.get_slice(
            session_id, purpose="act", limits={"max_turns": 4}
        )

        assert session is not None
        assert session["session_id"] == session_id
        assert session["active_agent_id"] == "hello-agent"
        assert turn_id in {turn["turn_id"] for turn in recent_turns}
        assert event_id in {
            event["event_id"] for event in store.get_events(session_id, limit=10)
        }
        assert any(item["session_id"] == session_id for item in sessions)
        assert session_slice["session_id"] == session_id
        assert session_slice["summary"]
    finally:
        try:
            store.close()
        finally:
            engine.dispose()
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()


@pytest.mark.postgres
def test_build_module_session_store_returns_postgres_store(tmp_path: Path) -> None:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"ssew_factory_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    try:
        store = build_module_session_store(
            config=StorageEngineConfig(
                root_dir=tmp_path / "storage",
                sqlite_path=tmp_path / "sessions.db",
                fallback_root=tmp_path,
                record_backend="record.postgres",
                record_backend_options={"url": _schema_url(postgres_url, schema_name)},
            ),
            database_path=tmp_path / "sessions.db",
            env={},
        )
        try:
            assert isinstance(store, PostgresSessionStore)
            assert isinstance(store, SessionStore)
        finally:
            store.close()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        admin_engine.dispose()
