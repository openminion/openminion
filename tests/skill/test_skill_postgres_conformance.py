from __future__ import annotations

from contextlib import ExitStack, closing
from pathlib import Path
import subprocess

import pytest

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.storage import build_skill_store
from openminion.modules.skill.storage.store import PostgresSkillStore, SQLiteSkillStore
from openminion.modules.skill.learning.runtime import (
    list_runtime_workflow_observations,
    observe_runtime_workflow,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)
from tests.skill.test_ingest_authority_contract import _config, _markdown, _operator


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


def test_skill_store_rejects_cross_agent_owner_change(skill_store_case) -> None:
    _backend, store = skill_store_case
    assert store.upsert_skill(
        skill_id="skill.owned",
        name="Owned",
        status="draft",
        scope="agent",
        agent_id="agent-a",
        ts="2026-04-01T00:00:00+00:00",
    )
    store.insert_skill_version(
        skill_id="skill.owned",
        version_hash="v1",
        source_artifact_ref="artifact://owned",
        package_json='{"name":"Owned","agent_id":"agent-a"}',
        created_at="2026-04-01T00:00:00+00:00",
    )
    assert not store.upsert_skill(
        skill_id="skill.owned",
        name="Takeover",
        status="draft",
        scope="agent",
        agent_id="agent-b",
        ts="2026-04-02T00:00:00+00:00",
    )
    rows = store.list_skills(agent_id="agent-a")
    assert rows[0]["agent_id"] == "agent-a"
    assert rows[0]["name"] == "Owned"


def test_skill_store_repository_owner_is_immutable(skill_store_case, tmp_path) -> None:
    _, store = skill_store_case
    first = str(tmp_path / "repo-a")
    second = str(tmp_path / "repo-b")
    for root, accepted in (
        (first, True),
        (first, True),
        (second, False),
        (None, False),
    ):
        assert (
            store.upsert_skill(
                skill_id="repo.skill",
                name="Repository skill",
                status="draft",
                scope="project",
                agent_id=None,
                repository_root=root,
                ts="2026-11-23T00:00:00Z",
            )
            is accepted
        )
    store.insert_skill_version(
        skill_id="repo.skill",
        version_hash="v1",
        source_artifact_ref="artifact://source",
        package_json='{"skill_id":"repo.skill","scope":"project"}',
        created_at="2026-11-23T00:00:00Z",
    )
    assert [
        row["skill_id"] for row in store.list_latest_skills(repository_root=first)
    ] == ["repo.skill"]
    assert store.list_latest_skills(repository_root=second) == []
    assert store.list_latest_skills() == []
    assert (
        store.get_skill_lifecycle_facts(skill_id="repo.skill")["repository_root"]
        == first
    )


def test_ingest_rejects_competing_owner_before_duplicate_return(
    skill_store_case, tmp_path, monkeypatch
):
    _, store = skill_store_case
    roots = [tmp_path / "repo-a", tmp_path / "repo-b"]
    for root in roots:
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    with (
        closing(Skill(_config(tmp_path / "a"))) as first,
        closing(Skill(_config(tmp_path / "b"))) as second,
    ):
        # Skill constructs SQLite by default; exercise its public ingest/admission on each backend.
        first.store = store
        second.store = type(store)(record_store=store._record_store)
        winner = {}

        def admit_competitor():
            if winner:
                return
            skill_id, version, _ = second.ingest_text(
                "Deploy",
                _markdown("Read."),
                scope="project",
                repository_root=str(roots[1]),
            )
            second.admit_skill_version(
                skill_id=skill_id,
                version_hash=version,
                expected_active_version_hash=None,
                target_status="verified",
                reason="reviewed competing fixture",
                authority=_operator(),
            )
            winner.update(
                second.store.get_skill_admission(
                    skill_id=skill_id, version_hash=version
                )
            )

        upsert = store.upsert_skill
        find = store.find_skill_version_by_fingerprint

        def upsert_after_competitor(**kwargs):
            admit_competitor()
            return upsert(**kwargs)

        def find_after_competitor(**kwargs):
            admit_competitor()
            return find(**kwargs)

        monkeypatch.setattr(store, "upsert_skill", upsert_after_competitor)
        monkeypatch.setattr(
            store, "find_skill_version_by_fingerprint", find_after_competitor
        )
        with pytest.raises(SkillError, match="SKILL_SCOPE_OWNER_CONFLICT"):
            first.ingest_text(
                "Deploy",
                _markdown("Read."),
                scope="project",
                repository_root=str(roots[0]),
            )
        assert winner
        assert second.get_skill_owner(skill_id="deploy")["repository_root"] == str(
            roots[1].resolve()
        )
        assert second.get_skill("deploy").version_hash == winner["version_hash"]
        assert (
            second.store.get_skill_admission(
                skill_id="deploy", version_hash=winner["version_hash"]
            )
            == winner
        )
        assert first.list_pending_skill_versions(repository_root=str(roots[0])) == []


def test_skill_admission_backend_parity(skill_store_case) -> None:
    _backend, store = skill_store_case
    store.upsert_skill(
        skill_id="skill.deploy",
        name="Deploy",
        status="draft",
        scope="global",
        agent_id=None,
        ts="2026-04-01T00:00:00+00:00",
    )
    store.insert_skill_version(
        skill_id="skill.deploy",
        version_hash="v1",
        source_artifact_ref="artifact://deploy",
        package_json='{"skill_id":"skill.deploy","version_hash":"v1","status":"draft"}',
        content_fingerprint="content-v1",
        created_at="2026-04-01T00:00:00+00:00",
    )
    store.stage_skill_version(
        skill_id="skill.deploy",
        version_hash="v1",
        content_fingerprint="content-v1",
        authority_class="runtime_untrusted",
        created_at="2026-04-01T00:00:00+00:00",
    )
    assert store.get_skill_package("skill.deploy") is None
    assert store.activate_skill_version(
        skill_id="skill.deploy",
        version_hash="v1",
        expected_active_version_hash=None,
        target_status="verified",
        authority_class="local_operator",
        reviewer_id="local:test",
        reason="reviewed",
        decided_at="2026-04-01T00:01:00+00:00",
        verification_check="manual bundle review",
        verification_result="passed",
        verification_evidence_ref="review://deploy/v1",
        verification_reviewer_id="local:test",
    )
    assert store.get_skill_package("skill.deploy")["status"] == "verified"
    admission = store.get_skill_admission(skill_id="skill.deploy", version_hash="v1")
    assert admission["verification_evidence_ref"] == "review://deploy/v1"
    assert admission["verification_reviewer_id"] == "local:test"


def test_workflow_observation_backend_parity(skill_store_case) -> None:
    _backend, store = skill_store_case
    created = store.insert_workflow_observation(
        agent_id="agent-a",
        source_run_ref="run-1",
        bundle_id="wlev-1",
        provenance_checksum="checksum-1",
        bundle_json='{"bundle_id":"wlev-1"}',
        created_at="2026-10-06T00:00:00+00:00",
    )
    duplicate = store.insert_workflow_observation(
        agent_id="agent-a",
        source_run_ref="run-1",
        bundle_id="wlev-2",
        provenance_checksum="checksum-2",
        bundle_json='{"bundle_id":"wlev-2"}',
        created_at="2026-10-06T00:01:00+00:00",
    )

    assert created is True
    assert duplicate is False
    assert (
        store.get_workflow_observation(agent_id="agent-a", source_run_ref="run-1")[
            "bundle_id"
        ]
        == "wlev-1"
    )
    assert [
        row["bundle_id"] for row in store.list_workflow_observations(agent_id="agent-a")
    ] == ["wlev-1"]


def test_runtime_workflow_observation_backend_parity(skill_store_case) -> None:
    _backend, store = skill_store_case

    def observe(*, agent_id: str, source_run_ref: str, intent_category: str = "modify"):
        return observe_runtime_workflow(
            store,
            agent_id=agent_id,
            source_run_ref=source_run_ref,
            intent_category=intent_category,
            capability_category="code",
            tool_names=["file.write", "exec.run"],
        )

    first = observe(agent_id="agent-a", source_run_ref="run-1")
    duplicate = observe(agent_id="agent-a", source_run_ref="run-1")
    second = observe(agent_id="agent-a", source_run_ref="run-2")
    conflict = observe(
        agent_id="agent-a",
        source_run_ref="run-1",
        intent_category="verify",
    )
    other_agent = observe(agent_id="agent-b", source_run_ref="run-1")

    assert first.status == "observed"
    assert duplicate.status == "duplicate"
    assert second.status == "authoring_ready"
    assert conflict.status == "conflict"
    assert other_agent.status == "observed"
    assert len(list_runtime_workflow_observations(store, agent_id="agent-a")) == 2
    assert len(list_runtime_workflow_observations(store, agent_id="agent-b")) == 1


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


@pytest.mark.postgres
def test_repository_migration_preserves_postgres_legacy_rows(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from openminion.modules.skill.storage import migrations

    with open_postgres_record_store("skcp03_migration") as (_, schema_name):
        storage = build_postgres_storage_config(
            tmp_path=tmp_path, schema_name=schema_name, sqlite_name="skill.db"
        )
        engine = create_engine(storage.record_backend_options["url"])
        storage_root = Path(migrations.__file__).resolve().parent.parent
        config = Config(str(storage_root / "alembic.ini"))
        config.set_main_option("script_location", str(storage_root / "migrations"))
        try:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0007_workflow_observations")
                connection.execute(
                    text(
                        "INSERT INTO skills(skill_id, name, status, scope, created_at, updated_at, active_version_hash) "
                        "VALUES ('legacy', 'Legacy', 'verified', 'project', 'then', 'then', 'v1')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO skill_versions(skill_id, version_hash, source_artifact_ref, package_json, created_at, content_fingerprint) "
                        "VALUES ('legacy', 'v1', 'artifact://legacy', '{}', 'then', 'original-fingerprint')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO skill_version_admissions(skill_id, version_hash, state, target_status, content_fingerprint, authority_class, created_at, reason) "
                        "VALUES ('legacy', 'v1', 'admitted', 'verified', 'original-fingerprint', 'local_operator', 'then', 'historical review')"
                    )
                )
                before = {
                    table: connection.execute(text(f"SELECT * FROM {table}"))
                    .mappings()
                    .all()
                    for table in (
                        "skills",
                        "skill_versions",
                        "skill_version_admissions",
                    )
                }
                command.upgrade(config, "head")
                assert (
                    connection.execute(
                        text("SELECT repository_root FROM skills")
                    ).scalar()
                    is None
                )
                for table, rows in before.items():
                    after = (
                        connection.execute(text(f"SELECT * FROM {table}"))
                        .mappings()
                        .all()
                    )
                    assert [
                        {
                            key: value
                            for key, value in row.items()
                            if key != "repository_root"
                        }
                        for row in after
                    ] == rows
                command.downgrade(config, "0007_workflow_observations")
                for table, rows in before.items():
                    assert (
                        connection.execute(text(f"SELECT * FROM {table}"))
                        .mappings()
                        .all()
                        == rows
                    )
        finally:
            engine.dispose()
