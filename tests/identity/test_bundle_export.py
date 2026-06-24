from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.identity.runtime.md_generator import (
    export_profile_to_markdown_bundle,
)
from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.runtime.lockfile import (
    IDENTITY_LOCKFILE_NAME,
    IdentityLockfile,
    build_lock_manifest,
    compute_tree_sha256,
    read_identity_lockfile,
    write_identity_lockfile,
)


def _profile(**overrides: object) -> AgentProfile:
    payload: dict[str, object] = {
        "agent_id": "ops-agent",
        "display_name": "Ops Agent",
        "profile_revision": 3,
        "role": {
            "mission": "Keep systems healthy.",
            "responsibilities": ["Watch service health", "Communicate incidents"],
            "hard_constraints": ["Never expose secrets"],
            "domain": [],
            "escalation_rules": ["Escalate on data loss risk"],
        },
        "personality": {
            "tone": "Calm and direct.",
            "verbosity": "normal",
            "formatting": ["Use short sections"],
            "interaction_style": ["Assume good intent"],
        },
        "risk": {
            "risk_level": "medium",
            "confirm_before": ["destructive_actions"],
            "auto_proceed_rules": [],
        },
        "tool_posture": {
            "tool_use": "allowed",
            "sandbox_root": None,
            "blocked_patterns": [],
            "allowed_tools": [],
        },
        "meta": {},
    }
    payload.update(overrides)
    return AgentProfile.model_validate(payload)


def test_export_profile_to_markdown_bundle_renders_agent_and_soul_sections() -> None:
    exported = export_profile_to_markdown_bundle(_profile())

    assert exported.agent_id == "ops-agent"
    assert [doc.relative_path for doc in exported.documents] == ["AGENT.md", "SOUL.md"]
    agent_doc = exported.documents[0].content
    soul_doc = exported.documents[1].content
    assert "## Mission" in agent_doc
    assert "- Watch service health" in agent_doc
    assert "## Constraints" in agent_doc
    assert "## Voice" in soul_doc
    assert "- Calm and direct." in soul_doc
    assert exported.lossy_fields == ()


def test_export_profile_to_markdown_bundle_reports_lossy_fields() -> None:
    exported = export_profile_to_markdown_bundle(
        _profile(
            inherits="base-agent",
            llm_policy_ref="policy-v2",
            allowed_capabilities=["web_search"],
            role={
                "mission": "Keep systems healthy.",
                "responsibilities": ["Watch service health"],
                "hard_constraints": ["Never expose secrets"],
                "domain": ["infrastructure"],
                "escalation_rules": [],
            },
            personality={
                "tone": "Calm and direct.",
                "verbosity": "detailed",
                "formatting": [],
                "interaction_style": [],
            },
            risk={
                "risk_level": "high",
                "confirm_before": ["destructive_actions", "data_delete"],
                "auto_proceed_rules": [],
            },
            tool_posture={
                "tool_use": "restricted",
                "sandbox_root": "/tmp",
                "blocked_patterns": ["rm -rf"],
                "allowed_tools": ["file.read"],
            },
            meta={"source": "yaml", "owner": "ops"},
        )
    )

    assert exported.lossy_fields == (
        "role.domain",
        "personality.verbosity",
        "risk.*",
        "tool_posture.*",
        "inherits",
        "llm_policy_ref",
        "allowed_capabilities",
        "meta.*",
    )


def test_identity_lockfile_round_trip_is_deterministic(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True)
    (bundle_root / "AGENT.md").write_text(
        "## Mission\nLockfile test\n", encoding="utf-8"
    )
    (bundle_root / "SOUL.md").write_text("## Voice\n- Steady\n", encoding="utf-8")
    (bundle_root / "SKILLS" / "ops").mkdir(parents=True)
    (bundle_root / "SKILLS" / "ops" / "SKILL.md").write_text(
        "## Purpose\nKeep it running\n",
        encoding="utf-8",
    )

    entries = build_lock_manifest(bundle_root)
    tree_hash = compute_tree_sha256(entries)
    lock = IdentityLockfile(
        generated_from_profile_version="abc123",
        generated_at="2026-03-21T00:00:00Z",
        files=entries,
        tree_sha256=tree_hash,
    )
    lock_path = bundle_root / IDENTITY_LOCKFILE_NAME
    write_identity_lockfile(lock_path, lock)
    reloaded = read_identity_lockfile(lock_path)

    assert reloaded == lock
    first_payload = lock_path.read_text(encoding="utf-8")
    write_identity_lockfile(lock_path, reloaded)
    second_payload = lock_path.read_text(encoding="utf-8")
    assert first_payload == second_payload
    parsed = json.loads(first_payload)
    assert [item["relative_path"] for item in parsed["files"]] == [
        "AGENT.md",
        "SKILLS/ops/SKILL.md",
        "SOUL.md",
    ]
