from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.skill.errors import SkillError
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


def _markdown(procedure: str, *, status: str | None = None) -> str:
    frontmatter = ["---", "name: Deploy", "id: deploy"]
    if status is not None:
        frontmatter.append(f"status: {status}")
    frontmatter.extend(
        [
            "verification:",
            "  - Confirm the result.",
            "---",
            "",
            "# Procedure",
            procedure,
            "",
            "# Verification",
            "- Confirm the result.",
        ]
    )
    return "\n".join(frontmatter)


def _operator() -> SkillIngestAuthority:
    return SkillIngestAuthority.local_operator(
        surface="test.skill.operator", principal_id="local:test"
    )


def test_runtime_ingest_rejects_catalog_visible_frontmatter(tmp_path: Path) -> None:
    ctl = Skill(_config(tmp_path))
    try:
        with pytest.raises(
            SkillError, match="SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED"
        ):
            ctl.ingest_text("Deploy", _markdown("Run A", status="verified"))
    finally:
        ctl.close()


def test_pending_version_is_explicitly_readable_but_not_catalog_visible(
    tmp_path: Path,
) -> None:
    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_text("Deploy", _markdown("Run A"))
        assert "admission.pending" in warnings
        assert ctl.get_skill(skill_id, version_hash).status == "draft"
        with pytest.raises(SkillError, match="NOT_FOUND"):
            ctl.get_skill(skill_id)
        assert ctl.list_skills({}) == []
    finally:
        ctl.close()


def test_operator_admission_replacement_and_rollback(tmp_path: Path) -> None:
    ctl = Skill(_config(tmp_path))
    authority = _operator()
    try:
        skill_id, version_a, _ = ctl.ingest_text(
            "Deploy", _markdown("Run A", status="blessed"), authority=authority
        )
        assert ctl.get_skill(skill_id, version_a).status == "draft"
        admitted_a = ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_a,
            expected_active_version_hash=None,
            target_status="verified",
            reason="initial review",
            authority=authority,
        )
        assert admitted_a["active_version_hash"] == version_a
        assert ctl.get_skill(skill_id).version_hash == version_a

        _, version_b, _ = ctl.ingest_text(
            "Deploy", _markdown("Run B"), authority=authority
        )
        assert version_b != version_a
        assert ctl.get_skill(skill_id).version_hash == version_a

        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_b,
            expected_active_version_hash=version_a,
            target_status="blessed",
            reason="replacement reviewed",
            authority=authority,
        )
        assert ctl.get_skill(skill_id).version_hash == version_b

        rolled_back = ctl.rollback_skill_version(
            skill_id=skill_id,
            to_version_hash=version_a,
            expected_active_version_hash=version_b,
            reason="regression",
            authority=authority,
        )
        assert rolled_back["active_version_hash"] == version_a
        assert ctl.get_skill(skill_id).version_hash == version_a

        stored = ctl.store.get_skill_package(skill_id, version_a)
        assert json.loads(json.dumps(stored))["status"] == "draft"
    finally:
        ctl.close()


def test_untrusted_authority_cannot_activate(tmp_path: Path) -> None:
    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text("Deploy", _markdown("Run A"))
        with pytest.raises(SkillError, match="SKILL_OPERATOR_AUTH_REQUIRED"):
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=version_hash,
                expected_active_version_hash=None,
                target_status="verified",
                reason="not authorized",
                authority=SkillIngestAuthority.runtime(
                    surface="test.runtime", source_kind="local"
                ),
            )
    finally:
        ctl.close()
