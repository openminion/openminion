from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus
import uuid

import pytest

from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.session_store import SessionStore

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
    finally:
        try:
            runtime_storage.close()
        finally:
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()
