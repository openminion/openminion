from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.skill.storage import build_skill_store
from openminion.modules.skill.storage.store import PostgresSkillStore, SQLiteSkillStore
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


@pytest.fixture(params=_backend_params())
def skill_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteSkillStore(tmp_path / "skill.db")
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt1_skill")
            )
            store = PostgresSkillStore(record_store=record_store)
        stack.callback(store.close)
        yield backend, store


def test_skill_store_round_trip(skill_store_case) -> None:
    _backend, store = skill_store_case
    package_json = '{"name":"frontend","entry":"SKILL.md"}'
    store.upsert_skill(
        skill_id="skill.frontend",
        name="Frontend",
        status="active",
        scope="global",
        agent_id=None,
        ts="2026-04-01T00:00:00+00:00",
    )
    store.insert_skill_version(
        skill_id="skill.frontend",
        version_hash="v1",
        source_artifact_ref="artifact://frontend",
        package_json=package_json,
        created_at="2026-04-01T00:00:00+00:00",
    )
    store.upsert_skill_index(
        skill_id="skill.frontend",
        version_hash="v1",
        tags_json='["ui"]',
        tools_json='["tool.shell"]',
        keywords_json='["react"]',
        applies_to_json='{"language":"ts"}',
    )
    store.insert_skill_run(
        run_id="run-1",
        session_id="sess-1",
        agent_id="hello-agent",
        skill_id="skill.frontend",
        version_hash="v1",
        used_for="selection",
        outcome="ok",
        evidence_refs_json='["artifact://frontend"]',
        created_at="2026-04-01T00:01:00+00:00",
    )

    assert store.get_skill_package("skill.frontend", "v1") == {
        "name": "frontend",
        "entry": "SKILL.md",
    }
    latest = store.list_latest_skills(status_filter=["active"])
    assert latest[0]["skill_id"] == "skill.frontend"
    assert store.list_skills(status_filter=["active"], scope="global")[0]["tags"] == [
        "ui"
    ]

    deleted = store.delete_skill(skill_id="skill.frontend", version_hash="v1")
    assert deleted["versions"] == 1
    assert store.get_skill_package("skill.frontend", "v1") is None


def test_build_skill_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_skill_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "skill.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "skill.db",
    )
    try:
        assert isinstance(store, SQLiteSkillStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_skill_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt1_skill_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_skill_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="skill.db",
            ),
            database_path=tmp_path / "skill.db",
        )
        try:
            assert isinstance(store, PostgresSkillStore)
        finally:
            store.close()
