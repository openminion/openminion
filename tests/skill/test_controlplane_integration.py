from __future__ import annotations

from pathlib import Path


SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "skills"


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "controlplane-skills.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["http_request", "file", "exec", "browser"],
        }
    }


class TestControlplaneIntegration:
    def test_skill_library_compatible_with_controlplane(self, tmp_path: Path):
        from openminion.modules.skill.runtime.skill import Skill

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
            assert "create account" in snippet.lower() or "account" in snippet.lower()
        finally:
            ctl.close()

    def test_concrete_flow_skill_has_required_steps(self, tmp_path: Path):
        from openminion.modules.skill.runtime.skill import Skill

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
                max_tokens=800,
            )

            assert "account" in snippet.lower()
            assert "post" in snippet.lower()
            assert "share" in snippet.lower() or "publish" in snippet.lower()
        finally:
            ctl.close()

    def test_controlplane_fixtures_available(self):
        fixtures_path = (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "controlplane"
            / "telegram"
            / "integration"
            / "fixtures.py"
        )

        assert fixtures_path.exists(), f"Fixtures file not found at {fixtures_path}"

        content = fixtures_path.read_text()

        assert "MockSkillBrain" in content, "MockSkillBrain class not found in fixtures"
        assert "SkillFlowRuntimeFixture" in content, (
            "SkillFlowRuntimeFixture class not found in fixtures"
        )
        assert "skill_flow_fixture" in content, (
            "skill_flow_fixture function not found in fixtures"
        )
        assert "create account" in content.lower(), (
            "create account handling not found in fixtures"
        )
        assert "create post" in content.lower(), (
            "create post handling not found in fixtures"
        )
        assert "share" in content.lower(), "share handling not found in fixtures"
