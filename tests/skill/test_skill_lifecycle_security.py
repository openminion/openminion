from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.skill import Skill


def _skill(tmp_path: Path, events: list[tuple[str, dict[str, object]]]) -> Skill:
    return Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "data" / "skill.db"),
                "blob_root": str(tmp_path / "data" / "blobs"),
                "fallback_root": str(tmp_path / "data" / "fallback"),
                "known_tools": ["tool.safe"],
                "wal": False,
            },
            "paths": {"data_root": str(tmp_path / "data")},
        },
        event_callback=lambda name, payload: events.append((name, payload)),
    )


def test_lifecycle_keeps_metadata_authority_separate_from_admission(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    ctl = _skill(tmp_path, events)
    authority = SkillIngestAuthority.local_operator(
        surface="test.lifecycle", principal_id="local:test"
    )
    markdown = """---
name: guarded-skill
id: guarded-skill
allowed-tools: [tool.admin]
---

# Procedure
Use the approved tools to complete the task.

# Verification
Confirm the expected result before completion.
"""
    try:
        skill_id, version_hash, _ = ctl.ingest_text("guarded-skill", markdown)
        assert ctl.list_skills({}) == []

        explicit = ctl.get_skill(skill_id, version_hash)
        assert explicit.status == "draft"
        assert explicit.tools == []
        assert explicit.bundle_metadata["agent_skills"]["allowed_tools"] == [
            "tool.admin"
        ]
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="operator reviewed content and permissions remain external",
            authority=authority,
        )
        assert ctl.get_skill(skill_id).version_hash == version_hash
        assert ctl.get_skill(skill_id).tools == []
        assert any(name == "skill.version_admitted" for name, _ in events)
    finally:
        ctl.close()
