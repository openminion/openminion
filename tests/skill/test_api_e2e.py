from __future__ import annotations

import os
from pathlib import Path

from openminion.modules.skill.runtime.skill import Skill


SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "skills"


def _cfg(tmp_path: Path) -> dict:
    data_root = os.getenv("OPENMINION_DATA_ROOT")
    if data_root:
        root = Path(data_root) / "skill" / "e2e" / tmp_path.name
        root.mkdir(parents=True, exist_ok=True)
        sqlite_path = root / "e2e-skills.db"
    else:
        sqlite_path = tmp_path / "e2e-skills.db"
    return {
        "skill": {
            "sqlite_path": str(sqlite_path),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["http_request", "file", "exec", "browser"],
        }
    }


def test_e2e_skill_ingest_and_match(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        path = SAMPLES_ROOT / "api-account-create-post-share" / "SKILL.md"
        skill_id, version_hash, warnings = ctl.ingest_file(
            path, name="api-account-create-post-share"
        )

        assert skill_id == "api-account-create-post-share"
        assert len(version_hash) == 64

        matches = ctl.match(
            intent_text="Create account and publish a post",
            step_hint={"risk": "medium", "verify": True, "tool_id": "http_request"},
            agent_id="agent.api",
            k=3,
        )

        assert matches
        assert any(item.skill_id == "api-account-create-post-share" for item in matches)

        snippet, snippet_hash = ctl.render_snippet(
            skill_id="api-account-create-post-share",
            version_hash=None,
            purpose="act",
            max_tokens=300,
        )

        assert snippet
        assert len(snippet_hash) == 64
        assert "api-account-create-post-share" in snippet

    finally:
        ctl.close()


def test_e2e_multi_skill_flow(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        path1 = SAMPLES_ROOT / "api-account-create-post-share" / "SKILL.md"
        path2 = SAMPLES_ROOT / "api-account-publish-share" / "SKILL.md"

        skill_id1, version_hash1, warnings1 = ctl.ingest_file(
            path1, name="api-account-create-post-share"
        )
        skill_id2, version_hash2, warnings2 = ctl.ingest_file(
            path2, name="api-account-publish-share"
        )

        assert skill_id1 == "api-account-create-post-share"
        assert skill_id2 == "api-account-publish-share"

        matches = ctl.match(
            intent_text="Publish to existing account",
            step_hint={"risk": "medium", "verify": True, "tool_id": "http_request"},
            agent_id="agent.api",
            k=3,
        )

        assert matches
        assert any(item.skill_id == "api-account-publish-share" for item in matches)

    finally:
        ctl.close()


def test_e2e_skill_version_tracking(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        path = SAMPLES_ROOT / "api-account-create-post-share" / "SKILL.md"

        skill_id1, version_hash1, _ = ctl.ingest_file(
            path, name="api-account-create-post-share"
        )
        skill_id2, version_hash2, _ = ctl.ingest_file(
            path, name="api-account-create-post-share"
        )

        assert len(version_hash1) == 64
        assert len(version_hash2) == 64

        snippet1, _ = ctl.render_snippet(
            skill_id="api-account-create-post-share",
            version_hash=version_hash1,
            purpose="act",
            max_tokens=200,
        )
        snippet2, _ = ctl.render_snippet(
            skill_id="api-account-create-post-share",
            version_hash=version_hash2,
            purpose="act",
            max_tokens=200,
        )

        assert snippet1
        assert snippet2

    finally:
        ctl.close()
