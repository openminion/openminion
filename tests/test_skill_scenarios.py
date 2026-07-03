from pathlib import Path


CLI_CHAT_SMOKE_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "skills" / "cli-chat-smoke"
)


class TestPlanSkillScenario:
    def test_plan_skill_selection_keywords(self):
        plan_skill_path = CLI_CHAT_SMOKE_DIR / "plan" / "SKILL.md"
        content = plan_skill_path.read_text()

        assert "when_to_use:" in content, "Missing when_to_use section"
        assert "plan" in content.lower(), "Missing plan trigger word"
        assert (
            "implementation plan" in content.lower() or "plan a task" in content.lower()
        ), "Missing plan task trigger"

    def test_plan_skill_checkpoint_structure(self):
        plan_skill_path = CLI_CHAT_SMOKE_DIR / "plan" / "SKILL.md"
        content = plan_skill_path.read_text()

        assert "checkpoint" in content.lower(), "Missing checkpoint in skill"
        assert "Contains 2-3 checkpoints" in content, (
            "Missing checkpoint count requirement"
        )
        assert "Action" in content, "Missing Action keyword"
        assert "Verify" in content, "Missing Verify keyword"
        assert "Next action" in content, "Missing Next action requirement"

    def test_plan_skill_has_file_tool(self):
        plan_skill_path = CLI_CHAT_SMOKE_DIR / "plan" / "SKILL.md"
        content = plan_skill_path.read_text()

        lines = content.split("\n")
        in_frontmatter = False
        found_tools_line = False
        for line in lines:
            if line.strip() == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter and line.startswith("tools:"):
                assert "file" in line, "Plan skill should have file tool"
                found_tools_line = True
                break

        assert found_tools_line, "Missing tools in frontmatter"


class TestDebugSkillScenario:
    def test_debug_skill_selection_keywords(self):
        debug_skill_path = CLI_CHAT_SMOKE_DIR / "debug" / "SKILL.md"
        content = debug_skill_path.read_text()

        assert (
            "debug" in content.lower()
            or "triage" in content.lower()
            or "error" in content.lower()
        ), "Missing debug trigger words"
        assert "when_to_use:" in content, "Missing when_to_use section"

    def test_debug_skill_checklist_structure(self):
        debug_skill_path = CLI_CHAT_SMOKE_DIR / "debug" / "SKILL.md"
        content = debug_skill_path.read_text()

        assert "checklist" in content.lower() or "steps" in content.lower(), (
            "Missing checklist/steps in skill"
        )
        assert "# Procedure" in content, "Missing Procedure section"

        step_count = content.count("## Step")
        assert step_count >= 2, (
            f"Debug skill should have at least 2 steps, found {step_count}"
        )

    def test_debug_skill_failure_recovery(self):
        debug_skill_path = CLI_CHAT_SMOKE_DIR / "debug" / "SKILL.md"
        content = debug_skill_path.read_text()

        assert "# Failure & Recovery" in content, "Missing Failure & Recovery section"
        assert "error" in content.lower() or "fail" in content.lower(), (
            "Missing error/failure handling keywords"
        )


class TestWebResearchSkillScenario:
    def test_web_research_skill_selection_keywords(self):
        research_skill_path = CLI_CHAT_SMOKE_DIR / "web-research" / "SKILL.md"
        content = research_skill_path.read_text()

        assert "research" in content.lower() or "search" in content.lower(), (
            "Missing research trigger words"
        )
        assert "web.search" in content, "Missing web.search tool reference"
        assert "when_to_use:" in content, "Missing when_to_use section"

    def test_web_research_skill_source_requirements(self):
        research_skill_path = CLI_CHAT_SMOKE_DIR / "web-research" / "SKILL.md"
        content = research_skill_path.read_text()

        assert "source" in content.lower(), "Missing source keyword"
        assert "# Procedure" in content, "Missing Procedure section"
        assert "# Checks" in content, "Missing Checks section"

    def test_web_research_skill_tools(self):
        research_skill_path = CLI_CHAT_SMOKE_DIR / "web-research" / "SKILL.md"
        content = research_skill_path.read_text()

        assert "tools:" in content, "Missing tools in frontmatter"
        assert "web.search" in content, "Missing web.search tool"


class TestApiPostSkillScenario:
    def test_api_post_skill_selection_keywords(self):
        api_skill_path = CLI_CHAT_SMOKE_DIR / "api-post" / "SKILL.md"
        content = api_skill_path.read_text()

        assert (
            "api" in content.lower()
            or "http" in content.lower()
            or "post" in content.lower()
        ), "Missing API trigger words"
        assert "when_to_use:" in content, "Missing when_to_use section"

    def test_api_post_skill_steps_structure(self):
        api_skill_path = CLI_CHAT_SMOKE_DIR / "api-post" / "SKILL.md"
        content = api_skill_path.read_text()

        assert "# Procedure" in content, "Missing Procedure section"
        assert "step" in content.lower(), "Missing step references"

        step_count = content.count("## Step")
        assert step_count >= 2, (
            f"API skill should have at least 2 steps, found {step_count}"
        )

    def test_api_post_skill_http_tool(self):
        api_skill_path = CLI_CHAT_SMOKE_DIR / "api-post" / "SKILL.md"
        content = api_skill_path.read_text()

        assert "tools:" in content, "Missing tools in frontmatter"
        assert "http_request" in content, "Missing http_request tool"


class TestSkillScenarioAssertions:
    def test_all_skills_have_distinct_ids(self):
        skill_ids = []

        for skill_dir in ["plan", "debug", "web-research", "api-post"]:
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            if not skill_path.exists():
                continue

            content = skill_path.read_text()
            lines = content.split("\n")

            for line in lines:
                if line.startswith("id:"):
                    skill_id = line.split(":", 1)[1].strip()
                    skill_ids.append((skill_dir, skill_id))
                    break

        ids_only = [sid for _, sid in skill_ids]
        assert len(ids_only) == len(set(ids_only)), (
            f"Duplicate skill IDs found: {skill_ids}"
        )

        expected_prefix = "cli-chat-smoke-"
        for skill_dir, skill_id in skill_ids:
            assert skill_id.startswith(expected_prefix), (
                f"Skill {skill_dir} ID should start with {expected_prefix}"
            )

    def test_all_skills_have_valid_version(self):
        for skill_dir in ["plan", "debug", "web-research", "api-post"]:
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            if not skill_path.exists():
                continue

            content = skill_path.read_text()
            lines = content.split("\n")

            version = None
            for line in lines:
                if line.startswith("version:"):
                    version = line.split(":", 1)[1].strip()
                    break

            assert version is not None, f"{skill_dir} missing version"
            parts = version.split(".")
            assert len(parts) >= 2, f"{skill_dir} invalid version format: {version}"
            assert all(p.isdigit() or p.isalnum() for p in parts[:2]), (
                f"{skill_dir} version should be numeric: {version}"
            )

    def test_skill_scenario_domain_markers(self):
        domain_markers = {
            "plan": ["checkpoint", "plan", "goal"],
            "debug": ["debug", "error", "checklist", "triage"],
            "web-research": ["research", "search", "source", "query"],
            "api-post": ["api", "http", "endpoint", "request"],
        }

        for skill_dir, markers in domain_markers.items():
            skill_path = CLI_CHAT_SMOKE_DIR / skill_dir / "SKILL.md"
            if not skill_path.exists():
                continue

            content = skill_path.read_text().lower()

            # At least one domain marker should be present
            has_marker = any(marker in content for marker in markers)
            assert has_marker, f"{skill_dir} missing domain markers: {markers}"
