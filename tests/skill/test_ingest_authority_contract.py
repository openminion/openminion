from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
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


def _markdown_without_verification(
    *, risk: str = "low", procedure: str = "Run A"
) -> str:
    return (
        f"---\nname: Deploy\nid: deploy\nrisk: {risk}\n---\n\n"
        f"# Procedure\n{procedure}\n"
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


@pytest.mark.parametrize(
    "authority_class", ["authenticated_api_operator", "internal_service"]
)
def test_only_local_operator_can_attach_verification_evidence(
    tmp_path: Path, authority_class: str
) -> None:
    ctl = Skill(_config(tmp_path))
    authority = SkillIngestAuthority(
        authority_class=authority_class,
        surface="test.non_local",
        source_kind="local",
        principal_id="test:service",
    )
    evidence = SkillVerificationEvidence(
        check="manual bundle review",
        result="passed",
        evidence_ref="review://deploy/v1",
    )
    try:
        skill_id, version_hash, _ = ctl.ingest_text("Deploy", _markdown("Run A"))
        with pytest.raises(SkillError, match="SKILL_OPERATOR_AUTH_REQUIRED"):
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=version_hash,
                expected_active_version_hash=None,
                target_status="verified",
                reason="reviewed",
                authority=authority,
                verification_evidence=evidence,
            )
        admission = ctl.store.get_skill_admission(
            skill_id=skill_id, version_hash=version_hash
        )
        assert admission["verification_check"] is None
    finally:
        ctl.close()


def test_operator_evidence_clears_only_status_verification_blocker(
    tmp_path: Path,
) -> None:
    ctl = Skill(_config(tmp_path))
    authority = _operator()
    evidence = SkillVerificationEvidence(
        check="manual bundle review",
        result="passed",
        evidence_ref="review://deploy/v1",
    )
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            "Deploy", _markdown_without_verification(), authority=authority
        )
        with pytest.raises(SkillError) as missing:
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=version_hash,
                expected_active_version_hash=None,
                target_status="verified",
                reason="reviewed",
                authority=authority,
            )
        assert missing.value.code == "SKILL_ADMISSION_VALIDATION_FAILED"
        assert missing.value.details["blockers"] == ["status.requires_verification"]

        admitted = ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="reviewed",
            authority=authority,
            verification_evidence=evidence,
        )
        assert admitted["verification_check"] == "manual bundle review"
        assert admitted["verification_reviewer_id"] == "local:test"

        _, high_risk_hash, _ = ctl.ingest_text(
            "Deploy",
            _markdown_without_verification(risk="high", procedure="Run B"),
            authority=authority,
        )
        with pytest.raises(SkillError) as high_risk:
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=high_risk_hash,
                expected_active_version_hash=version_hash,
                target_status="verified",
                reason="reviewed",
                authority=authority,
                verification_evidence=evidence,
            )
        assert high_risk.value.details["blockers"] == ["verification.required"]
    finally:
        ctl.close()


def test_rollback_preserves_verification_evidence_author(tmp_path: Path) -> None:
    ctl = Skill(_config(tmp_path))
    authority = _operator()
    evidence = SkillVerificationEvidence(
        check="manual bundle review",
        result="passed",
        evidence_ref="review://deploy/v1",
    )
    try:
        skill_id, version_a, _ = ctl.ingest_text(
            "Deploy", _markdown_without_verification(), authority=authority
        )
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_a,
            expected_active_version_hash=None,
            target_status="verified",
            reason="initial review",
            authority=authority,
            verification_evidence=evidence,
        )
        _, version_b, _ = ctl.ingest_text(
            "Deploy", _markdown("Run B"), authority=authority
        )
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_b,
            expected_active_version_hash=version_a,
            target_status="verified",
            reason="replacement review",
            authority=authority,
        )
        rollback_authority = SkillIngestAuthority.local_operator(
            surface="test.skill.rollback", principal_id="local:rollback"
        )

        result = ctl.rollback_skill_version(
            skill_id=skill_id,
            to_version_hash=version_a,
            expected_active_version_hash=version_b,
            reason="rollback",
            authority=rollback_authority,
        )
        admission = ctl.store.get_skill_admission(
            skill_id=skill_id, version_hash=version_a
        )

        assert result["reviewer_id"] == "local:rollback"
        assert result["verification_reviewer_id"] == "local:test"
        assert admission["reviewer_id"] == "local:rollback"
        assert admission["verification_reviewer_id"] == "local:test"
        assert admission["verification_evidence_ref"] == "review://deploy/v1"
    finally:
        ctl.close()


def test_stale_admission_conflict_does_not_write_verification_evidence(
    tmp_path: Path,
) -> None:
    ctl = Skill(_config(tmp_path))
    authority = _operator()
    evidence = SkillVerificationEvidence(
        check="manual bundle review",
        result="passed",
        evidence_ref="review://deploy/v2",
    )
    try:
        skill_id, version_a, _ = ctl.ingest_text(
            "Deploy", _markdown("Run A"), authority=authority
        )
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_a,
            expected_active_version_hash=None,
            target_status="verified",
            reason="reviewed",
            authority=authority,
        )
        _, version_b, _ = ctl.ingest_text(
            "Deploy",
            _markdown_without_verification(procedure="Run B"),
            authority=authority,
        )

        with pytest.raises(SkillError, match="SKILL_ACTIVE_VERSION_CONFLICT"):
            ctl.admit_skill_version(
                skill_id=skill_id,
                version_hash=version_b,
                expected_active_version_hash="stale-version",
                target_status="verified",
                reason="reviewed",
                authority=authority,
                verification_evidence=evidence,
            )

        admission = ctl.store.get_skill_admission(
            skill_id=skill_id, version_hash=version_b
        )
        assert admission["state"] == "pending"
        assert admission["verification_check"] is None
        assert admission["verification_evidence_ref"] is None
    finally:
        ctl.close()
