from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess

import pytest

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.contracts import ContextCtlSkillAdapter
from openminion.modules.skill.runtime.skill import Skill
from tests.skill.test_ingest_authority_contract import _config, _markdown, _operator
from tests.skill.test_skill_admission_migration import _run_alembic


@pytest.fixture
def repositories(tmp_path):
    roots = (tmp_path / "repo-a", tmp_path / "repo-b")
    for root in roots:
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    return tuple(str(root.resolve()) for root in roots)


@pytest.fixture
def skill(tmp_path):
    with closing(Skill(_config(tmp_path))) as ctl:
        yield ctl


def _admit(skill, skill_id, version, expected=None):
    return skill.admit_skill_version(
        skill_id=skill_id,
        version_hash=version,
        expected_active_version_hash=expected,
        target_status="verified",
        reason="reviewed fixture",
        authority=_operator(),
    )


def test_repository_visibility_and_runtime_reads(skill, repositories):
    first, second = repositories
    versions = {}
    for skill_id, scope, agent, root in (
        ("global", "global", None, None),
        ("mine", "agent", "agent-a", None),
        ("other", "agent", "agent-b", None),
        ("repo-a", "project", "agent-a", first),
        ("repo-b", "project", "agent-a", second),
    ):
        _, version, _ = skill.ingest_text(
            skill_id,
            _markdown("Read facts.").replace("id: deploy", f"id: {skill_id}"),
            scope=scope,
            agent_id=agent,
            repository_root=root,
        )
        _admit(skill, skill_id, version)
        versions[skill_id] = version
    for root, expected in (
        (None, {"global", "mine"}),
        (first, {"global", "mine", "repo-a"}),
        (second, {"global", "mine", "repo-b"}),
    ):
        assert {
            r["id"] for r in skill.catalog_summaries("agent-a", repository_root=root)
        } == expected
        assert {
            r["skill_id"]
            for r in skill.list_skills({"agent_id": "agent-a"}, repository_root=root)
        } == expected
        for skill_id, version in versions.items():
            if skill_id in expected:
                assert (
                    skill.get_runtime_skill(
                        skill_id, version, agent_id="agent-a", repository_root=root
                    ).version_hash
                    == version
                )
                snippet, _ = skill.render_snippet(
                    skill_id,
                    version,
                    "act",
                    256,
                    agent_id="agent-a",
                    repository_root=root,
                )
                assert f"Skill ID: {skill_id}" in snippet
            else:
                with pytest.raises(SkillError, match="NOT_FOUND"):
                    skill.get_runtime_skill(
                        skill_id, version, agent_id="agent-a", repository_root=root
                    )
                with pytest.raises(SkillError, match="NOT_FOUND"):
                    skill.render_snippet(
                        skill_id,
                        version,
                        "act",
                        256,
                        agent_id="agent-a",
                        repository_root=root,
                    )
    assert skill.get_skill_owner(skill_id="repo-a") == {
        "scope": "project",
        "agent_id": None,
        "repository_root": first,
    }
    assert skill.match("repo-a", None, "agent-a", repository_root=second) == []
    assert [
        m.skill_id
        for m in skill.match("repo-a", None, "agent-a", repository_root=first)
    ] == ["repo-a"]


@pytest.mark.parametrize("procedure", ["Read A.", "Read B."])
def test_repository_owner_conflict_precedes_dedup(skill, repositories, procedure):
    first, second = repositories
    skill_id, version, _ = skill.ingest_text(
        "Deploy", _markdown("Read A."), scope="project", repository_root=first
    )
    _admit(skill, skill_id, version)
    admission = skill.store.get_skill_admission(skill_id=skill_id, version_hash=version)
    with pytest.raises(SkillError, match="SKILL_SCOPE_OWNER_CONFLICT"):
        skill.ingest_text(
            "Deploy", _markdown(procedure), scope="project", repository_root=second
        )
    assert skill.get_skill_owner(skill_id=skill_id)["repository_root"] == first
    assert skill.get_skill(skill_id).version_hash == version
    assert (
        skill.store.get_skill_admission(skill_id=skill_id, version_hash=version)
        == admission
    )
    assert skill.list_pending_skill_versions(repository_root=second) == []


def test_runtime_reads_preserve_global_and_null_agent_behavior(skill):
    global_id, global_version, _ = skill.ingest_text("Deploy", _markdown("Read."))
    _admit(skill, global_id, global_version)
    agent_id, agent_version, _ = skill.ingest_text(
        "Legacy", _markdown("Read.").replace("id: deploy", "id: legacy"), scope="agent"
    )
    _admit(skill, agent_id, agent_version)
    assert {r["id"] for r in skill.catalog_summaries("agent-a")} == {
        global_id,
        agent_id,
    }
    for current_agent in ("", "agent-a"):
        assert (
            skill.get_runtime_skill(global_id, agent_id=current_agent).version_hash
            == global_version
        )
        with pytest.raises(SkillError, match="NOT_FOUND"):
            skill.get_runtime_skill(agent_id, agent_id=current_agent)


def test_repository_correction_admission_and_canonical_duplicate(skill, repositories):
    root, other = repositories
    child = Path(root) / "child"
    child.mkdir()
    skill_id, first, _ = skill.ingest_text(
        "Deploy", _markdown("Read A."), scope="project", repository_root=str(child)
    )
    _admit(skill, skill_id, first)
    _, duplicate, warnings = skill.ingest_text(
        "Deploy", _markdown("Read A."), scope="project", repository_root=root
    )
    assert duplicate == first and "admission.duplicate_content" in warnings
    _, second, _ = skill.ingest_text(
        "Deploy", _markdown("Read B."), scope="project", repository_root=root
    )
    assert second != first
    adapter = ContextCtlSkillAdapter(skill)
    assert [
        match.skill_id
        for match in adapter.match("Deploy", None, "agent-a", repository_root=root)
    ] == [skill_id]
    assert "Read B." in adapter.render_snippet(skill_id, second, "act", 256)[0]
    with pytest.raises(SkillError, match="NOT_FOUND"):
        adapter.render_snippet(
            skill_id, second, "act", 256, agent_id="agent-a", repository_root=root
        )
    assert (
        "Read A."
        in adapter.render_snippet(
            skill_id, first, "act", 256, agent_id="agent-a", repository_root=root
        )[0]
    )
    assert (
        skill.get_runtime_skill(
            skill_id, agent_id="agent-a", repository_root=root
        ).version_hash
        == first
    )
    with pytest.raises(SkillError, match="NOT_FOUND"):
        skill.get_runtime_skill(
            skill_id, second, agent_id="agent-a", repository_root=root
        )
    assert [
        row["version_hash"]
        for row in skill.list_pending_skill_versions(
            {"scope": "project"}, repository_root=root
        )
    ] == [second]
    assert skill.list_pending_skill_versions(repository_root=other) == []
    with pytest.raises(SkillError, match="SKILL_ACTIVE_VERSION_CONFLICT"):
        _admit(skill, skill_id, second, "stale")
    _admit(skill, skill_id, second, first)
    skill.rollback_skill_version(
        skill_id=skill_id,
        to_version_hash=first,
        expected_active_version_hash=second,
        reason="fixture rollback",
        authority=_operator(),
    )
    assert skill.get_skill(skill_id).version_hash == first


@pytest.mark.parametrize("root", [None, "", "relative"])
def test_project_ingest_requires_explicit_repository(skill, root):
    with pytest.raises(SkillError, match="INVALID_ARGUMENT"):
        skill.ingest_text(
            "Deploy", _markdown("Read."), scope="project", repository_root=root
        )
    assert skill.get_skill_owner(skill_id="deploy") is None


def test_supplied_non_repository_is_invalid_for_ingest_and_queries(skill, tmp_path):
    root = str(tmp_path)
    with pytest.raises(SkillError, match="INVALID_ARGUMENT"):
        skill.ingest_text(
            "Deploy", _markdown("Read."), scope="project", repository_root=root
        )
    for query in (
        lambda: skill.catalog_summaries("agent-a", repository_root=root),
        lambda: skill.list_skills(repository_root=root),
        lambda: skill.list_pending_skill_versions(repository_root=root),
        lambda: skill.match("Deploy", None, "agent-a", repository_root=root),
        lambda: skill.workflow_catalog(repository_root=root),
        lambda: skill.get_runtime_skill(
            "deploy", agent_id="agent-a", repository_root=root
        ),
    ):
        with pytest.raises(SkillError, match="INVALID_ARGUMENT"):
            query()


def test_repository_binding_does_not_change_package_hashes(
    tmp_path, repositories, monkeypatch
):
    monkeypatch.setattr(
        "openminion.modules.skill.runtime.skill.ingest.iso_now",
        lambda: "2026-11-23T00:00:00Z",
    )
    packages = []
    for index, root in enumerate(repositories):
        with closing(Skill(_config(tmp_path / str(index)))) as skill:
            skill_id, version, _ = skill.ingest_text(
                "Deploy", _markdown("Read."), scope="project", repository_root=root
            )
            package = skill.get_skill(skill_id, version)
            assert "repository_root" not in package.to_dict()
            packages.append(package)
    assert packages[0].to_dict() == packages[1].to_dict()
    assert packages[0].to_content_fingerprint() == packages[1].to_content_fingerprint()


def test_repository_migration_preserves_legacy_content_and_admission(
    tmp_path, repositories
):
    config = _config(tmp_path)
    database = tmp_path / "skill.db"
    _run_alembic(database, "upgrade", "head")
    with closing(Skill(config)) as skill:
        skill_id, version, _ = skill.ingest_text(
            "Deploy",
            _markdown("Read."),
            scope="project",
            repository_root=repositories[0],
        )
        _admit(skill, skill_id, version)
    _run_alembic(database, "downgrade", "0007_workflow_observations")
    with sqlite3.connect(database) as connection:
        before = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("skills", "skill_versions", "skill_version_admissions")
        }
    _run_alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT repository_root FROM skills").fetchall() == [
            (None,)
        ]
        for table, rows in before.items():
            after = connection.execute(f"SELECT * FROM {table}").fetchall()
            assert ([row[:-1] for row in after] if table == "skills" else after) == rows
    with closing(Skill(config)) as skill:
        assert skill.catalog_summaries("agent-a", repository_root=repositories[0]) == []
        assert skill.get_skill(skill_id).version_hash == version
        with pytest.raises(SkillError, match="NOT_FOUND"):
            skill.get_runtime_skill(
                skill_id, agent_id="agent-a", repository_root=repositories[0]
            )
        with pytest.raises(SkillError, match="SKILL_SCOPE_OWNER_CONFLICT"):
            skill.ingest_text(
                "Deploy",
                _markdown("Read."),
                scope="project",
                repository_root=repositories[0],
            )
