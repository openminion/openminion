from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
import sqlalchemy as sa

from openminion.cli.commands.storage import (
    _get_validated_module_ids,
    run_storage_migrate,
    run_storage_verify,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres


TIER3_VALIDATED_MODULES = {
    "secret",
    "session",
    "telemetry",
    "identity",
    "registry",
    "task",
    "skill",
    "controlplane",
    "policy",
    "compress",
    "retrieve",
    "artifact",
    "a2a",
    "memory",
}


@pytest.mark.postgres
def test_postgres_tier3_modules_are_validated() -> None:
    to_run, skipped = _get_validated_module_ids("postgres", None)
    assert set(to_run) == TIER3_VALIDATED_MODULES
    assert "storage" in skipped


@pytest.mark.postgres
def test_storage_cli_plan_and_verify_cover_tier3_modules(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"mpt3_integration_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    try:
        scoped_url = schema_url(postgres_url, schema_name)
        run_storage_migrate(
            SimpleNamespace(
                backend="postgres",
                postgres_url=scoped_url,
                sqlite=str(tmp_path / "openminion.db"),
                module=None,
                plan=True,
                json=True,
            )
        )
        plan_payload = json.loads(capsys.readouterr().out)
        plan_rows = {row["module_id"]: row["status"] for row in plan_payload["modules"]}
        for module_id in TIER3_VALIDATED_MODULES:
            assert plan_rows[module_id] == "plan"

        run_storage_verify(
            SimpleNamespace(
                backend="postgres",
                postgres_url=scoped_url,
                sqlite=str(tmp_path / "openminion.db"),
                module=None,
                json=True,
            )
        )
        verify_payload = json.loads(capsys.readouterr().out)
        verify_rows = {
            row["module_id"]: row["status"] for row in verify_payload["modules"]
        }
        for module_id in TIER3_VALIDATED_MODULES:
            assert verify_rows[module_id] == "passed"
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.postgres
def test_postgres_memory_search_end_to_end() -> None:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"mpt3_memory_e2e_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sa.create_engine(schema_url(postgres_url, schema_name), future=True)
    try:
        store = PostgresMemoryStore(
            engine,
            database_path=Path.cwd() / ".openminion-memory-postgres-e2e",
        )
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for idx in range(10):
            store.put(
                MemoryRecord(
                    id=f"mem-{idx}",
                    scope="agent:e2e",
                    type="fact",
                    title=f"city weather {idx}",
                    content=f"weather for japanese city {idx} right now",
                    tags=["weather", "japan"],
                    entities=[f"City{idx}"],
                    created_at=now,
                    updated_at=now,
                )
            )
        hits = store.search(
            SearchQueryOptions(query="weather japan", scopes=["agent:e2e"], limit=10)
        )
        assert len(hits) == 10
        assert all("tsrank_score" in item.meta for item in hits)
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
