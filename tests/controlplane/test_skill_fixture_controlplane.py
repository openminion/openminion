import pytest
from pathlib import Path


CLI_CHAT_SMOKE_DIR = (
    Path(__file__).resolve().parents[2] / "examples" / "skills" / "cli-chat-smoke"
)


class TestControlplaneSkillFixture:
    def test_controlplane_skill_ingest_available(self):
        from openminion.modules.controlplane.runtime import RuntimeCoordinator

        assert hasattr(RuntimeCoordinator, "handle_inbound"), (
            "RuntimeCoordinator missing handle_inbound"
        )

    def test_controlplane_skill_fixture_path_resolution(self):
        for skill_dir in ["plan", "debug", "web-research", "api-post"]:
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            assert skill_path.exists(), f"Fixture not found: {skill_path}"
            assert skill_path.is_absolute(), f"Path not absolute: {skill_path}"

    def test_controlplane_skill_metadata_extraction(self):
        plan_skill_path = CLI_CHAT_SMOKE_DIR / "plan" / "SKILL.md"
        content = plan_skill_path.read_text()

        lines = content.split("\n")
        in_frontmatter = False
        frontmatter = {}
        for line in lines:
            if line.strip() == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter and ":" in line:
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()

        assert "id" in frontmatter, "Missing skill_id for routing"
        assert "tags" in frontmatter, "Missing tags for routing"
        assert "tools" in frontmatter, "Missing tools for capability check"

    def test_controlplane_skill_selection_keywords(self):
        domain_keywords = {
            "plan": ["plan", "checkpoint"],
            "debug": ["debug", "error", "triage"],
            "web-research": ["research", "search", "source"],
            "api-post": ["api", "http", "endpoint"],
        }

        for skill_dir, keywords in domain_keywords.items():
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            content = skill_path.read_text().lower()

            has_keywords = any(kw in content for kw in keywords)
            assert has_keywords, f"{skill_dir} missing domain keywords: {keywords}"

    def test_controlplane_fixture_skill_ids(self):
        expected_prefix = "cli-chat-smoke-"

        for skill_dir in ["plan", "debug", "web-research", "api-post"]:
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            content = skill_path.read_text()
            lines = content.split("\n")

            skill_id = None
            for line in lines:
                if line.startswith("id:"):
                    skill_id = line.split(":", 1)[1].strip()
                    break

            assert skill_id is not None, f"{skill_dir} missing skill_id"
            assert skill_id.startswith(expected_prefix), (
                f"{skill_dir} ID should start with {expected_prefix}"
            )
            assert skill_id.endswith(skill_dir), (
                f"{skill_dir} ID should end with domain name"
            )


class TestControlplaneSkillLifecycle:
    def test_controlplane_skill_lifecycle_stages(self):
        from openminion.modules.skill import Skill
        from openminion.modules.skill.runtime import parser as skill_parser

        skill_ctl = Skill({})
        assert hasattr(skill_ctl, "ingest_file"), "Missing ingest_file"

        assert callable(getattr(skill_parser, "parse_markdown", None)), (
            "Missing parser.parse_markdown function"
        )

        assert hasattr(skill_ctl, "list_skills"), "Missing list_skills"
        assert hasattr(skill_ctl, "get_skill"), "Missing get_skill"

        skill_ctl.close()

    def test_controlplane_skill_error_handling(self):
        from openminion.modules.skill import Skill
        from openminion.modules.skill.errors import SkillError

        skill_ctl = Skill({})

        with pytest.raises((SkillError, FileNotFoundError)):
            skill_ctl.ingest_file("/nonexistent/path/SKILL.md")

        skill_ctl.close()


class TestControlplaneSkillParity:
    def test_skill_contract_parity(self):
        plan_skill_path = CLI_CHAT_SMOKE_DIR / "plan" / "SKILL.md"
        content = plan_skill_path.read_text()

        required_sections = [
            "# Skill Card",
            "# Procedure",
            "# Checks",
            "# Failure & Recovery",
        ]

        for section in required_sections:
            assert section in content, f"Missing required section: {section}"

    def test_fixture_set_consistency(self):
        expected_fixtures = ["plan", "debug", "web-research", "api-post"]

        for fixture in expected_fixtures:
            skill_path = CLI_CHAT_SMOKE_DIR / fixture / "SKILL.md"
            assert skill_path.exists(), f"Missing fixture: {fixture}"

            content = skill_path.read_text()
            assert "---" in content, f"{fixture} missing frontmatter"
            assert "id:" in content, f"{fixture} missing id"
