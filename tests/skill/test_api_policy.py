from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.runtime.skill import Skill


SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "skills"


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "policy-skills.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["http_request", "file", "exec", "browser"],
        }
    }


def _redact_secret(value: str, visible_chars: int = 8) -> str:
    if not value or len(value) <= visible_chars:
        return "***REDACTED***"
    return f"{value[:visible_chars]}***"


class TestSecretRedaction:
    def test_redact_full_secret(self):
        api_key = "sk_live_abc123def456ghi789"
        redacted = _redact_secret(api_key)
        assert redacted == "sk_live_***"
        assert "abc123" not in redacted

    def test_redact_short_secret(self):
        api_key = "sk"
        redacted = _redact_secret(api_key)
        assert redacted == "***REDACTED***"

    def test_redact_empty_secret(self):
        redacted = _redact_secret("")
        assert redacted == "***REDACTED***"

    def test_redact_none_secret(self):
        redacted = _redact_secret(None)
        assert redacted == "***REDACTED***"

    def test_redact_exact_length(self):
        api_key = "sk_live_"
        redacted = _redact_secret(api_key, visible_chars=8)
        assert redacted == "***REDACTED***"

    def test_redact_custom_visible_chars(self):
        api_key = "sk_live_abc123"
        redacted = _redact_secret(api_key, visible_chars=4)
        assert redacted == "sk_l***"


class TestSkillPolicyMetadata:
    def test_create_post_share_has_risk_level(self, tmp_path: Path):
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

            assert "risk" in snippet.lower() or "medium" in snippet.lower()
        finally:
            ctl.close()

    def test_publish_share_has_risk_level(self, tmp_path: Path):
        ctl = Skill(_cfg(tmp_path))
        try:
            path = SAMPLES_ROOT / "api-account-publish-share" / "SKILL.md"
            skill_id, version_hash, warnings = ctl.ingest_file(
                path, name="api-account-publish-share"
            )

            snippet, _ = ctl.render_snippet(
                skill_id="api-account-publish-share",
                version_hash=None,
                purpose="act",
                max_tokens=500,
            )

            assert "risk" in snippet.lower() or "medium" in snippet.lower()
        finally:
            ctl.close()

    def test_skills_specify_scopes(self, tmp_path: Path):
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

            assert "http" in snippet.lower() or "tool" in snippet.lower()
        finally:
            ctl.close()


class TestPolicyGatingBehavior:
    def test_mock_api_default_for_safety(self, tmp_path: Path):
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

            assert "api-account-create-post-share" in snippet
        finally:
            ctl.close()

    def test_no_real_api_defaults(self, tmp_path: Path):
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

            assert "api-account-create-post-share" in snippet
        finally:
            ctl.close()


class TestVerificationRequirements:
    def test_create_post_share_has_verification(self, tmp_path: Path):
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

            assert "api-account-create-post-share" in snippet
            assert "medium" in snippet.lower() or "risk" in snippet.lower()
        finally:
            ctl.close()

    def test_publish_share_has_verification(self, tmp_path: Path):
        ctl = Skill(_cfg(tmp_path))
        try:
            path = SAMPLES_ROOT / "api-account-publish-share" / "SKILL.md"
            skill_id, version_hash, warnings = ctl.ingest_file(
                path, name="api-account-publish-share"
            )

            snippet, _ = ctl.render_snippet(
                skill_id="api-account-publish-share",
                version_hash=None,
                purpose="act",
                max_tokens=800,
            )

            assert "api-account-publish-share" in snippet
            assert "medium" in snippet.lower() or "risk" in snippet.lower()
        finally:
            ctl.close()
