from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from openminion.cli.commands.scaffold import _skill_files
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.modules.skill.runtime.skill import Skill

HELLO_SKILL = (
    Path(__file__).resolve().parents[2] / "examples" / "skills" / "hello" / "SKILL.md"
)


def _config(tmp_path: Path) -> dict[str, object]:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "blob_root": str(tmp_path / "blob"),
            "fallback_root": str(tmp_path / "fallback"),
            "wal": False,
        }
    }


def _operator() -> SkillIngestAuthority:
    return SkillIngestAuthority.local_operator(
        surface="test.skill.identity", principal_id="local:test"
    )


def _write_skill(root: Path, folder: str, markdown: str) -> Path:
    path = root / folder / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def _hello_layout(skill_id: str) -> str:
    markdown = HELLO_SKILL.read_text(encoding="utf-8")
    assert "`skill_id`: `hello.greet`" in markdown
    return markdown.replace("`hello.greet`", f"`{skill_id}`")


def test_markdown_first_skills_keep_their_metadata_skill_ids(tmp_path: Path) -> None:
    other = _write_skill(tmp_path, "other", _hello_layout("other.probe"))
    ctl = Skill(_config(tmp_path))
    try:
        hello_id, hello_hash, _ = ctl.ingest_file(HELLO_SKILL, authority=_operator())
        other_id, other_hash, _ = ctl.ingest_file(other, authority=_operator())

        assert (hello_id, other_id) == ("hello.greet", "other.probe")
        assert ctl.get_skill(other_id, other_hash).skill_id == "other.probe"
        with pytest.raises(SkillError, match="NOT_FOUND"):
            ctl.get_skill(hello_id, other_hash)
    finally:
        ctl.close()


def test_scaffolded_skill_ingests_under_its_metadata_skill_id(tmp_path: Path) -> None:
    template = _skill_files("release-notes", agent_id=None)
    path = _write_skill(
        tmp_path, "release_notes", template["skills/release_notes/SKILL.md"]
    )
    ctl = Skill(_config(tmp_path))
    try:
        skill_id, _, _ = ctl.ingest_file(path, authority=_operator())

        assert skill_id == "release_notes"
    finally:
        ctl.close()


def test_front_matter_id_remains_the_explicit_identity(tmp_path: Path) -> None:
    alpha = _write_skill(
        tmp_path,
        "alpha",
        "---\nname: alpha\nid: verify.alpha\n---\n\n# Procedure\nSay alpha.\n",
    )
    both = _write_skill(
        tmp_path,
        "both",
        "---\nname: beta\nid: verify.beta\n---\n\n" + _hello_layout("metadata.beta"),
    )
    ctl = Skill(_config(tmp_path))
    try:
        assert ctl.ingest_file(alpha, authority=_operator())[0] == "verify.alpha"
        assert ctl.ingest_file(both, authority=_operator())[0] == "verify.beta"
    finally:
        ctl.close()


def test_pending_version_is_listed_for_operators_but_not_selectable(
    tmp_path: Path,
) -> None:
    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            HELLO_SKILL, authority=_operator()
        )

        assert "admission.pending" in warnings
        pending = ctl.list_pending_skill_versions({"status": ["draft"]})
        assert [
            (
                row["skill_id"],
                row["version_hash"],
                row["status"],
                row["admission_state"],
            )
            for row in pending
        ] == [(skill_id, version_hash, "draft", "pending")]
        assert ctl.list_pending_skill_versions({"status": ["verified"]}) == []
        assert ctl.get_skill(skill_id, version_hash).status == "draft"

        assert ctl.list_skills({}) == []
        assert ctl.catalog_summaries(agent_id="agent.main") == []
        assert ctl.match(skill_id, None, agent_id="agent.main") == []
        with pytest.raises(SkillError, match="NOT_FOUND"):
            ctl.get_skill(skill_id)
    finally:
        ctl.close()


def test_operator_admission_is_the_only_path_into_selection(tmp_path: Path) -> None:
    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_file(HELLO_SKILL, authority=_operator())

        admitted = ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="reviewed the hello example",
            authority=_operator(),
            verification_evidence=SkillVerificationEvidence(
                check="manual review", result="passed", evidence_ref="review://hello"
            ),
        )

        assert admitted["previous_active_version_hash"] is None
        assert admitted["active_version_hash"] == version_hash
        assert ctl.list_pending_skill_versions({}) == []
        assert [row["skill_id"] for row in ctl.list_skills({})] == [skill_id]
        assert [row["id"] for row in ctl.catalog_summaries(agent_id="agent.main")] == [
            skill_id
        ]
        assert [
            match.skill_id for match in ctl.match(skill_id, None, agent_id="agent.main")
        ] == [skill_id]
        assert ctl.get_skill(skill_id).status == "verified"
    finally:
        ctl.close()


def test_same_skill_id_isolated_across_explicit_stores(tmp_path: Path) -> None:
    configs = (_config(tmp_path / "store-a"), _config(tmp_path / "store-b"))
    procedures = ("Read repository A facts.", "Read repository B facts.")
    paths = []
    for folder, procedure in zip(("repo-a", "repo-b"), procedures, strict=True):
        path = _write_skill(
            tmp_path,
            f"{folder}/skills/shared",
            "---\nname: shared\nid: shared.probe\n---\n\n"
            f"# Procedure\n{procedure}\n\n"
            "# Verification\nConfirm the reference matches the repository.\n",
        )
        (path.parent / "references").mkdir()
        (path.parent / "references/facts.txt").write_text(procedure, encoding="utf-8")
        paths.append(path)

    versions = []
    packages = []
    with closing(Skill(configs[0])) as first, closing(Skill(configs[1])) as second:
        for ctl, path in zip((first, second), paths, strict=True):
            skill_id, version, warnings = ctl.ingest_file(
                path, scope="agent", agent_id="agent.main", authority=_operator()
            )
            assert skill_id == "shared.probe"
            assert "admission.pending" in warnings
            assert ctl.catalog_summaries("agent.main") == []
            assert ctl.get_skill(skill_id, version).status == "draft"
            versions.append(version)
        assert versions[0] != versions[1]

        for ctl, version in zip((first, second), versions, strict=True):
            assert second.catalog_summaries("agent.main") == []
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=version,
                expected_active_version_hash=None,
                target_status="verified",
                reason="reviewed the repository fixture",
                authority=_operator(),
            )
            assert [
                (row["id"], row["version_hash"])
                for row in ctl.catalog_summaries("agent.main")
            ] == [(skill_id, version)]
            packages.append(ctl.get_skill(skill_id).to_dict())

    for config, version, foreign_version, procedure, package in zip(
        configs, versions, reversed(versions), procedures, packages, strict=True
    ):
        with closing(Skill(config)) as ctl:
            assert ctl.get_skill(skill_id).to_dict() == package
            assert procedure in ctl.get_skill(skill_id).sections["procedure"]
            assert [
                (row["id"], row["version_hash"])
                for row in ctl.catalog_summaries("agent.main")
            ] == [(skill_id, version)]
            assert (
                ctl.read_skill_resource(
                    skill_id=skill_id,
                    version_hash=version,
                    resource_path="references/facts.txt",
                )["content"]
                == procedure
            )
            with pytest.raises(SkillError, match="NOT_FOUND"):
                ctl.get_skill(skill_id, foreign_version)
