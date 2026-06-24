from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


_DRAFT_SKILL = """
---
name: Trust Test Skill
id: trust_test_skill
status: draft
tools: [tool.shell]
risk: low
---

## Summary
Minimal draft skill used for trust-state tests.

## Procedure
- tool.shell run "echo hello"
""".strip()

_VERIFIED_SKILL = """
---
name: Trust Test Skill
id: trust_test_skill
status: verified
tools: [tool.shell]
risk: low
verification:
  - echo hello
---

## Summary
Minimal verified skill used for trust-state tests.

## Procedure
- tool.shell run "echo hello"

## Verification
- tool.shell run "echo hello"
""".strip()


def _run(coro):
    return asyncio.run(coro)


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-trust.db"),
            "blob_root": str(tmp_path / "blob"),
            "fallback_root": str(tmp_path / "fallback"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["tool.shell"],
        }
    }


def test_ingest_text_defaults_to_untrusted_local(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _warnings = ctl.ingest_text(
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
        )
        package = ctl.get_skill(skill_id, version_hash)
        assert package.bundle_metadata["trust"] == "untrusted_local"
    finally:
        ctl.close()


def test_ingest_artifact_defaults_to_untrusted_local(tmp_path: Path) -> None:
    ctl = Skill(
        _cfg(tmp_path),
        artifact_loader=lambda _ref: _DRAFT_SKILL,
    )
    try:
        skill_id, version_hash, _warnings = ctl.ingest_artifact(
            "artifact://trust-test",
            name="Trust Test Skill",
        )
        package = ctl.get_skill(skill_id, version_hash)
        assert package.bundle_metadata["trust"] == "untrusted_local"
    finally:
        ctl.close()


def test_ingest_url_defaults_to_untrusted_remote(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _warnings = ctl.ingest_url(
            url="https://example.com/SKILL.md",
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
        )
        package = ctl.get_skill(skill_id, version_hash)
        assert package.bundle_metadata["trust"] == "untrusted_remote"
    finally:
        ctl.close()


def test_ingest_runtime_rejects_invalid_trust(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as excinfo:
            ctl.ingest_text(
                name="Trust Test Skill",
                markdown=_DRAFT_SKILL,
                trust="mystery",
            )
        assert excinfo.value.code == "INVALID_ARGUMENT"
        assert "bundle_metadata.trust must be one of" in excinfo.value.message
    finally:
        ctl.close()


def test_untrusted_local_promotion_emits_audit_event(tmp_path: Path) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = Skill(_cfg(tmp_path), telemetryctl=TelemetryCtl(telemetry))
    ctl.set_telemetry_context(session_id="sess-trust", turn_id="turn-1")
    try:
        skill_id, _version_hash, _warnings = ctl.ingest_text(
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
        )
        updated = ctl.set_skill_status(
            skill_id=skill_id,
            new_status="verified",
            promotion_path="runtime",
        )
        assert updated.status == "verified"

        summary = _run(telemetry.get_module_summary("sess-trust"))
        stats = summary["openminion-skill"]
        assert stats["operation_counts"]["untrusted_source_promotion"] == 1
    finally:
        ctl.close()
        _run(telemetry.close())


def test_trusted_local_promotion_is_silent(tmp_path: Path) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = Skill(_cfg(tmp_path), telemetryctl=TelemetryCtl(telemetry))
    ctl.set_telemetry_context(session_id="sess-trust-silent", turn_id="turn-1")
    try:
        skill_id, _version_hash, _warnings = ctl.ingest_text(
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
            trust="trusted_local",
        )
        ctl.set_skill_status(
            skill_id=skill_id,
            new_status="verified",
            promotion_path="runtime",
        )

        summary = _run(telemetry.get_module_summary("sess-trust-silent"))
        stats = summary.get("openminion-skill")
        if stats is None:
            return
        assert stats["operation_counts"].get("untrusted_source_promotion", 0) == 0
    finally:
        ctl.close()
        _run(telemetry.close())


def test_untrusted_remote_runtime_promotion_fails_closed(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, _version_hash, _warnings = ctl.ingest_url(
            url="https://example.com/SKILL.md",
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
        )
        with pytest.raises(SkillError) as excinfo:
            ctl.set_skill_status(
                skill_id=skill_id,
                new_status="verified",
                promotion_path="runtime",
            )
        assert excinfo.value.code == "INVALID_ARGUMENT"
        assert "reviewer_id must be operator-supplied" in excinfo.value.message
    finally:
        ctl.close()


def test_untrusted_remote_operator_promotion_succeeds(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, _version_hash, _warnings = ctl.ingest_url(
            url="https://example.com/SKILL.md",
            name="Trust Test Skill",
            markdown=_DRAFT_SKILL,
        )
        updated = ctl.set_skill_status(
            skill_id=skill_id,
            new_status="verified",
            promotion_path="operator",
        )
        assert updated.status == "verified"
    finally:
        ctl.close()


def test_runtime_ingest_url_rejects_untrusted_remote_verified_skill(
    tmp_path: Path,
) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as excinfo:
            ctl.ingest_url(
                url="https://example.com/SKILL.md",
                name="Trust Test Skill",
                markdown=_VERIFIED_SKILL,
                promotion_path="runtime",
            )
        assert excinfo.value.code == "INVALID_ARGUMENT"
        assert "reviewer_id must be operator-supplied" in excinfo.value.message
    finally:
        ctl.close()
