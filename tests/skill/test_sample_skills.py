from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.runtime.skill import Skill

SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "skills"


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "sample-skills.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["file", "exec", "web.search", "browser", "reactions"],
        }
    }


def test_ingest_all_sample_skills_without_lint_errors(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        samples = sorted(SAMPLES_ROOT.glob("*/SKILL.md"))
        assert samples, "expected sample SKILL.md files"

        for path in samples:
            skill_id, version_hash, warnings = ctl.ingest_file(
                path, name=path.parent.name
            )
            assert skill_id
            assert len(version_hash) == 64
            assert not any(item.startswith("lint.error:") for item in warnings), (
                path,
                warnings,
            )
    finally:
        ctl.close()


def test_plan_sample_matches_plan_intent(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        for path in sorted(SAMPLES_ROOT.glob("*/SKILL.md")):
            ctl.ingest_file(path, name=path.parent.name)

        matches = ctl.match(
            intent_text="Create an implementation plan with checkpoints and verification",
            step_hint={"risk": "low", "verify": False, "tool_id": "file"},
            agent_id="agent.docs",
            k=3,
        )

        assert matches
        assert any(item.skill_id == "plan-checkpoints" for item in matches)

        snippet, snippet_hash = ctl.render_snippet(
            skill_id="plan-checkpoints",
            version_hash=None,
            purpose="plan",
            max_tokens=160,
        )
        assert snippet
        assert len(snippet_hash) == 64
    finally:
        ctl.close()


def test_api_account_create_post_share_ingest(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        path = SAMPLES_ROOT / "api-account-create-post-share" / "SKILL.md"
        skill_id, version_hash, warnings = ctl.ingest_file(
            path, name="api-account-create-post-share"
        )

        assert skill_id == "api-account-create-post-share"
        assert len(version_hash) == 64
        assert not any(item.startswith("lint.error:") for item in warnings), warnings
    finally:
        ctl.close()


def test_api_account_publish_share_ingest(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        path = SAMPLES_ROOT / "api-account-publish-share" / "SKILL.md"
        skill_id, version_hash, warnings = ctl.ingest_file(
            path, name="api-account-publish-share"
        )

        assert skill_id == "api-account-publish-share"
        assert len(version_hash) == 64
        assert not any(item.startswith("lint.error:") for item in warnings), warnings
    finally:
        ctl.close()


def test_api_skills_match_intents(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        for path in sorted(SAMPLES_ROOT.glob("*/SKILL.md")):
            ctl.ingest_file(path, name=path.parent.name)

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
        assert "http_request" in snippet.lower() or "post" in snippet.lower()
    finally:
        ctl.close()


def test_api_skills_have_required_sections(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        path = SAMPLES_ROOT / "api-account-create-post-share" / "SKILL.md"
        skill_id, version_hash, warnings = ctl.ingest_file(
            path, name="api-account-create-post-share"
        )

        snippet, _ = ctl.render_snippet(
            skill_id="api-account-create-post-share",
            version_hash=None,
            purpose="act",
            max_tokens=500,
        )

        assert "api-account-create-post-share" in snippet
        assert "http_request" in snippet.lower() or "file" in snippet.lower()
        assert "medium" in snippet.lower() or "risk" in snippet.lower()
    finally:
        ctl.close()
