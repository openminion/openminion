from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.skill import Skill


_DRAFT_SKILL = """
---
name: Trust Test Skill
id: trust_test_skill
status: draft
tools: [tool.shell]
risk: low
verification:
  - echo hello
---

## Summary
Minimal draft skill used for trust-state tests.

## Procedure
- tool.shell run "echo hello"

## Verification
- tool.shell run "echo hello"
""".strip()

_VERIFIED_SKILL = _DRAFT_SKILL.replace("status: draft", "status: verified")


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-trust.db"),
            "blob_root": str(tmp_path / "blob"),
            "fallback_root": str(tmp_path / "fallback"),
            "wal": False,
            "known_tools": ["tool.shell"],
        }
    }


def _operator(*, source_kind: str = "local") -> SkillIngestAuthority:
    return SkillIngestAuthority.local_operator(
        surface="test.skill.trust",
        principal_id="local:test",
        source_kind=source_kind,
    )


def test_ingest_text_defaults_to_untrusted_local(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text("Trust Test Skill", _DRAFT_SKILL)
        package = ctl.get_skill(skill_id, version_hash)
        assert package.bundle_metadata["trust"] == "untrusted_local"
        assert ctl.list_skills({}) == []
    finally:
        ctl.close()


def test_ingest_artifact_defaults_to_untrusted_local(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path), artifact_loader=lambda _ref: _DRAFT_SKILL)
    try:
        skill_id, version_hash, _ = ctl.ingest_artifact(
            "artifact://trust-test", name="Trust Test Skill"
        )
        assert (
            ctl.get_skill(skill_id, version_hash).bundle_metadata["trust"]
            == "untrusted_local"
        )
    finally:
        ctl.close()


def test_ingest_url_defaults_to_untrusted_remote(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_url(
            url="https://example.com/SKILL.md",
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
        )
        assert (
            ctl.get_skill(skill_id, version_hash).bundle_metadata["trust"]
            == "untrusted_remote"
        )
    finally:
        ctl.close()


def test_runtime_rejects_trust_override(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as excinfo:
            ctl.ingest_text("Trust Test Skill", _DRAFT_SKILL, trust="trusted_local")
        assert excinfo.value.code == "SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED"
    finally:
        ctl.close()


def test_runtime_cannot_promote_staged_version(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text("Trust Test Skill", _DRAFT_SKILL)
        with pytest.raises(SkillError) as excinfo:
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=version_hash,
                expected_active_version_hash=None,
                target_status="verified",
                reason="runtime attempt",
                authority=SkillIngestAuthority.runtime(
                    surface="test.runtime", source_kind="local"
                ),
            )
        assert excinfo.value.code == "SKILL_OPERATOR_AUTH_REQUIRED"
    finally:
        ctl.close()


def test_operator_may_attest_trust_then_explicitly_admit(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    authority = _operator()
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            "Trust Test Skill",
            _DRAFT_SKILL,
            trust="trusted_local",
            authority=authority,
        )
        assert (
            ctl.get_skill(skill_id, version_hash).bundle_metadata["trust"]
            == "trusted_local"
        )
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="reviewed",
            authority=authority,
        )
        assert ctl.get_skill(skill_id).status == "verified"
    finally:
        ctl.close()


def test_runtime_url_rejects_catalog_visible_frontmatter(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as excinfo:
            ctl.ingest_url(
                url="https://example.com/SKILL.md",
                name="Trust Test Skill",
                markdown=_VERIFIED_SKILL,
            )
        assert excinfo.value.code == "SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED"
    finally:
        ctl.close()
