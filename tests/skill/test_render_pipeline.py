from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.runtime.parser import parse_markdown


class TestRenderPipeline:
    @pytest.fixture
    def skill(self):
        return Skill(config={})

    @pytest.fixture
    def example_skills(self):
        repo_root = Path(__file__).resolve().parents[2]
        skills_dir = repo_root / "examples" / "skills"
        return sorted(glob.glob(str(skills_dir / "*" / "SKILL.md")))

    def test_all_skills_render_plan_purpose(self, skill, example_skills):
        results = []
        for path in example_skills:
            name = os.path.basename(os.path.dirname(path))
            sid, vh, issues = skill.ingest_file(path, name=name)
            text, hash_val = skill.render_snippet(sid, vh, "plan", 1500)
            results.append((name, len(text), text))
            assert len(text) >= 200, f"{name} render too short: {len(text)} chars"

        print(f"\nAll {len(results)} skills rendered >= 200 chars for plan purpose")

    def test_all_skills_render_act_purpose(self, skill, example_skills):
        for path in example_skills:
            name = os.path.basename(os.path.dirname(path))
            sid, vh, issues = skill.ingest_file(path, name=name)
            text, hash_val = skill.render_snippet(sid, vh, "act", 1500)
            assert len(text) >= 100, f"{name} act render too short: {len(text)} chars"

    def test_all_skills_render_verify_purpose(self, skill, example_skills):
        for path in example_skills:
            name = os.path.basename(os.path.dirname(path))
            sid, vh, issues = skill.ingest_file(path, name=name)
            text, hash_val = skill.render_snippet(sid, vh, "verify", 1500)
            assert len(text) >= 100, (
                f"{name} verify render too short: {len(text)} chars"
            )

    def test_examples_preserve_only_authored_recipes_and_known_tool_bindings(
        self, skill, example_skills
    ):
        for path in example_skills:
            name = os.path.basename(os.path.dirname(path))
            front_matter, _, _, _ = parse_markdown(Path(path).read_text())
            sid, vh, issues = skill.ingest_file(path, name=name)
            recipe = skill.get_recipe(sid, vh)
            authored = front_matter.get("recipe")
            if authored is None:
                assert recipe is None, name
                continue

            assert recipe is not None, name
            assert [(step.step_id, step.instruction) for step in recipe.steps] == [
                (step["step_id"], step["instruction"]) for step in authored["steps"]
            ], name
            assert [step.tool_id for step in recipe.steps] == [
                step.get("tool_id")
                if step.get("tool_id") in skill.config.known_tools
                else None
                for step in authored["steps"]
            ], name

    def test_render_respects_max_tokens(self, skill, example_skills):
        path = example_skills[0]
        name = os.path.basename(os.path.dirname(path))
        sid, vh, issues = skill.ingest_file(path, name=name)

        text_1500, _ = skill.render_snippet(sid, vh, "plan", 1500)
        text_100, _ = skill.render_snippet(sid, vh, "plan", 100)
        text_50, _ = skill.render_snippet(sid, vh, "plan", 50)

        assert len(text_1500) >= len(text_100) >= len(text_50), (
            "max_tokens budget not respected"
        )
        print(
            f"\nmax_tokens respected: 1500->{len(text_1500)}, 100->{len(text_100)}, 50->{len(text_50)}"
        )

    def test_rendered_text_contains_procedure_body(self, skill, example_skills):
        for path in example_skills:
            name = os.path.basename(os.path.dirname(path))
            sid, vh, issues = skill.ingest_file(path, name=name)
            text, _ = skill.render_snippet(sid, vh, "plan", 1500)

            has_step = "step" in text.lower() or "procedure" in text.lower()
            assert has_step or len(text) > 200, f"{name} render missing procedure body"
