from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from openminion.modules.skill.runtime.skill import Skill


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

    def test_get_recipe_returns_non_none(self, skill, example_skills):
        recipe_count = 0
        for path in example_skills:
            name = os.path.basename(os.path.dirname(path))
            sid, vh, issues = skill.ingest_file(path, name=name)
            recipe = skill.get_recipe(sid, vh)
            if recipe is not None:
                recipe_count += 1

        assert recipe_count >= 5, (
            f"Only {recipe_count}/9 skills have recipes (expected >= 5)"
        )
        print(f"\n{recipe_count}/9 skills have recipes")

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
