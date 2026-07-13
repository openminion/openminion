from pathlib import Path


CLI_CHAT_SMOKE_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "skills" / "cli-chat-smoke"
)
CLI_CHAT_INVALID_DIR = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "skills"
    / "cli-chat-smoke-invalid"
)


class TestValidFixtures:
    def test_plan_skill_fixture_exists(self):
        skill_path = CLI_CHAT_SMOKE_DIR / "plan" / "SKILL.md"
        assert skill_path.exists(), f"Plan skill fixture not found: {skill_path}"

        content = skill_path.read_text()
        assert "---" in content, "Missing YAML frontmatter"
        assert "id: cli-chat-smoke-plan" in content, "Missing skill id"
        assert "# Skill Card" in content, "Missing Skill Card section"
        assert "# Procedure" in content, "Missing Procedure section"
        assert "# Checks" in content, "Missing Checks section"
        assert "# Failure & Recovery" in content, "Missing Failure & Recovery section"

    def test_debug_skill_fixture_exists(self):
        skill_path = CLI_CHAT_SMOKE_DIR / "debug" / "SKILL.md"
        assert skill_path.exists(), f"Debug skill fixture not found: {skill_path}"

        content = skill_path.read_text()
        assert "id: cli-chat-smoke-debug" in content, "Missing skill id"
        assert "debugging" in content.lower(), "Missing debugging domain markers"

    def test_web_research_skill_fixture_exists(self):
        skill_path = CLI_CHAT_SMOKE_DIR / "web-research" / "SKILL.md"
        assert skill_path.exists(), (
            f"Web-research skill fixture not found: {skill_path}"
        )

        content = skill_path.read_text()
        assert "id: cli-chat-smoke-web-research" in content, "Missing skill id"
        assert "web.search" in content, "Missing web.search tool reference"

    def test_api_post_skill_fixture_exists(self):
        skill_path = CLI_CHAT_SMOKE_DIR / "api-post" / "SKILL.md"
        assert skill_path.exists(), f"API-post skill fixture not found: {skill_path}"

        content = skill_path.read_text()
        assert "id: cli-chat-smoke-api-post" in content, "Missing skill id"
        assert "http_request" in content, "Missing http_request tool reference"

    def test_fixture_index_exists(self):
        index_path = CLI_CHAT_SMOKE_DIR / "README.md"
        assert index_path.exists(), f"Fixture index not found: {index_path}"

        content = index_path.read_text()
        assert "cli-chat-smoke-plan" in content, "Missing plan skill in index"
        assert "cli-chat-smoke-debug" in content, "Missing debug skill in index"
        assert "cli-chat-smoke-web-research" in content, (
            "Missing web-research skill in index"
        )
        assert "cli-chat-smoke-api-post" in content, "Missing api-post skill in index"


class TestNegativeFixtures:
    def test_missing_sections_fixture_exists(self):
        skill_path = CLI_CHAT_INVALID_DIR / "missing-sections" / "SKILL.md"
        assert skill_path.exists(), f"Missing-sections fixture not found: {skill_path}"

        content = skill_path.read_text()
        assert content.count("## Step") < 3, "Should have incomplete Procedure"

    def test_malformed_headings_fixture_exists(self):
        skill_path = CLI_CHAT_INVALID_DIR / "malformed-headings" / "SKILL.md"
        assert skill_path.exists(), (
            f"Malformed-headings fixture not found: {skill_path}"
        )

        content = skill_path.read_text()
        lines = content.split("\n")
        in_frontmatter = False
        frontmatter_lines = []
        for line in lines:
            if line.strip() == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter:
                frontmatter_lines.append(line)

        has_malformed = any(
            ": " not in line and line.strip() for line in frontmatter_lines[:10]
        )
        assert has_malformed, "Expected malformed YAML frontmatter"

    def test_invalid_tools_fixture_exists(self):
        skill_path = CLI_CHAT_INVALID_DIR / "invalid-tools" / "SKILL.md"
        assert skill_path.exists(), f"Invalid-tools fixture not found: {skill_path}"

        content = skill_path.read_text()
        assert "nonexistent_tool_123" in content, "Missing invalid tool reference"
        assert "another_fake_tool" in content, "Missing another invalid tool reference"

    def test_negative_fixture_index_exists(self):
        index_path = CLI_CHAT_INVALID_DIR / "README.md"
        assert index_path.exists(), f"Negative fixture index not found: {index_path}"


class TestFixtureSchema:
    def _parse_frontmatter(self, content: str) -> dict:
        lines = content.split("\n")
        if lines[0].strip() != "---":
            return {}

        frontmatter = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" in line:
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()
        return frontmatter

    def test_valid_fixtures_have_required_frontmatter(self):
        required_fields = ["id", "name", "version", "description", "tags", "tools"]

        for skill_dir in ["plan", "debug", "web-research", "api-post"]:
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            if not skill_path.exists():
                continue

            content = skill_path.read_text()
            frontmatter = self._parse_frontmatter(content)

            for field in required_fields:
                assert field in frontmatter, (
                    f"{skill_dir}/SKILL.md missing frontmatter field: {field}"
                )

    def test_valid_fixtures_have_required_sections(self):
        required_sections = [
            "# Skill Card",
            "# Procedure",
            "# Checks",
            "# Failure & Recovery",
        ]

        for skill_dir in ["plan", "debug", "web-research", "api-post"]:
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            if not skill_path.exists():
                continue

            content = skill_path.read_text()

            for section in required_sections:
                assert section in content, (
                    f"{skill_dir}/SKILL.md missing section: {section}"
                )
