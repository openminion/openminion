from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.skill import Skill


def _config(tmp_path: Path) -> dict[str, object]:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "blob_root": str(tmp_path / "blob"),
            "fallback_root": str(tmp_path / "fallback"),
            "wal": False,
        }
    }


def test_agent_skills_metadata_and_resources_are_passive_and_progressive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "portable-skill"
    (root / "references").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "scripts").mkdir()
    (root / "references" / "guide.md").write_text("Reference details", encoding="utf-8")
    (root / "assets" / "template.txt").write_text("template", encoding="utf-8")
    (root / "scripts" / "check.py").write_text(
        "print('not executed')", encoding="utf-8"
    )
    skill_path = root / "SKILL.md"
    skill_path.write_text(
        """---
name: portable-skill
description: Use portable resources safely.
license: Apache-2.0
compatibility: Requires a POSIX-like shell.
metadata:
  author: OpenMinion
allowed-tools: tool.shell tool.fetch
---

# Procedure
Read references/guide.md before acting.
""",
        encoding="utf-8",
    )

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            skill_path,
            authority=SkillIngestAuthority.local_operator(
                surface="test.agent_skills", principal_id="local:test"
            ),
        )
        assert "admission.pending" in warnings
        package = ctl.get_skill(skill_id, version_hash)
        portable = package.bundle_metadata["agent_skills"]
        assert portable["license"] == "Apache-2.0"
        assert portable["allowed_tools"] == ["tool.shell", "tool.fetch"]
        assert package.tools == []
        assert {item["path"] for item in package.resources} == {
            "references/guide.md",
            "assets/template.txt",
            "scripts/check.py",
        }
        assert all(item["executable"] is False for item in package.resources)

        loaded = ctl.read_skill_resource(
            skill_id=skill_id,
            version_hash=version_hash,
            resource_path="references/guide.md",
            max_chars=9,
        )
        assert loaded["content"] == "Reference"
        assert loaded["truncated"] is True
    finally:
        ctl.close()


def test_bundle_symlink_is_not_admitted_as_a_resource(tmp_path: Path) -> None:
    root = tmp_path / "portable-skill"
    (root / "references").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "references" / "outside.txt").symlink_to(outside)
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(skill_path)
        assert "bundle.resources.symlink_skipped" in warnings
        assert ctl.get_skill(skill_id, version_hash).resources == []
    finally:
        ctl.close()
