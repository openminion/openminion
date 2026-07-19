from __future__ import annotations

from pathlib import Path

from openminion.modules.context.constants import PINNED_BUCKETS, TRIM_ORDER
from openminion.modules.context.schemas import default_budgets_for
from openminion.modules.context.segment import (
    ContextSegment,
    apply_trim_ladder,
    PackingDecisionLog,
)
from openminion.modules.context.service import _apply_mode_budget_bias
from openminion.modules.skill.runtime.skill import Skill


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-budget.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["tool.shell"],
        }
    }


DEPLOY_SKILL = """
---
name: Deploy Checker
id: deploy_checker
status: verified
tags: [deploy, ops]
tools: [tool.shell]
risk: low
applies_to:
  intents: [check deploy, verify deployment]
---

# Summary
Check deployment status on production.

# Procedure
- tool.shell run "kubectl get pods -n production"
- tool.shell run "kubectl rollout status deployment/app -n production"
- tool.shell run "curl -s http://localhost:8080/health"
""".strip()


class TestSkillBudgetDefaults:
    def test_decide_budget_is_raised_for_skill_visibility(self) -> None:
        budgets = default_budgets_for("decide")
        assert budgets.skills_tokens == 120

    def test_plan_budget_is_generous(self) -> None:
        budgets = default_budgets_for("plan")
        assert budgets.skills_tokens == 250

    def test_act_budget_is_generous(self) -> None:
        budgets = default_budgets_for("act")
        assert budgets.skills_tokens == 250

    def test_reflect_budget_is_small(self) -> None:
        budgets = default_budgets_for("reflect")
        assert budgets.skills_tokens == 80


class TestSkillModeOverrides:
    def test_plan_mode_bumps_to_320(self) -> None:
        budgets = default_budgets_for("plan")
        overridden = _apply_mode_budget_bias(budgets, mode_name="plan")
        assert overridden.skills_tokens >= 320

    def test_act_mode_bumps_to_240(self) -> None:
        budgets = default_budgets_for("act")
        overridden = _apply_mode_budget_bias(budgets, mode_name="act")
        assert overridden.skills_tokens >= 240

    def test_respond_mode_does_not_bump_skills(self) -> None:
        budgets = default_budgets_for("decide")
        overridden = _apply_mode_budget_bias(budgets, mode_name="respond")
        assert overridden.skills_tokens == budgets.skills_tokens


class TestSkillSnippetFitsInBudget:
    def test_snippet_within_plan_budget(self, tmp_path: Path) -> None:
        ctl = Skill(_cfg(tmp_path))
        try:
            skill_id, version_hash, _ = ctl.ingest_text(
                name="Deploy Checker", markdown=DEPLOY_SKILL
            )
            budgets = default_budgets_for("plan")
            snippet, _ = ctl.render_snippet(
                skill_id=skill_id,
                version_hash=version_hash,
                purpose="plan",
                max_tokens=budgets.skills_tokens,
            )
            assert snippet
            # Rough token estimate: ~4 chars per token
            estimated_tokens = len(snippet) // 4
            assert estimated_tokens <= budgets.skills_tokens * 2  # generous margin
        finally:
            ctl.close()

    def test_snippet_within_decide_budget_may_be_truncated(
        self, tmp_path: Path
    ) -> None:
        ctl = Skill(_cfg(tmp_path))
        try:
            skill_id, version_hash, _ = ctl.ingest_text(
                name="Deploy Checker", markdown=DEPLOY_SKILL
            )
            snippet, _ = ctl.render_snippet(
                skill_id=skill_id,
                version_hash=version_hash,
                purpose="decide",
                max_tokens=120,
            )
            assert snippet
            assert "Skill:" in snippet
        finally:
            ctl.close()


class TestTrimLadderBehavior:
    def test_retrieval_bucket_is_not_pinned(self) -> None:
        assert "retrieval" not in PINNED_BUCKETS

    def test_retrieval_is_in_trim_order(self) -> None:
        assert "retrieval" in TRIM_ORDER

    def test_retrieval_trimmed_before_recent_window(self) -> None:
        retrieval_idx = TRIM_ORDER.index("retrieval")
        recent_idx = TRIM_ORDER.index("recent_window")
        assert retrieval_idx < recent_idx

    def test_skill_segment_survives_when_under_budget(self) -> None:
        segments = [
            ContextSegment(
                id="retrieval:skill:deploy_checker",
                bucket="retrieval",
                content="[SKILL SNIPPET]\ntool.shell: kubectl get pods",
                token_estimate=max(
                    1, len("[SKILL SNIPPET]\ntool.shell: kubectl get pods") // 4
                ),
                pinned=False,
            ),
            ContextSegment(
                id="turn_input:user",
                bucket="turn_input",
                content="check deploy status",
                token_estimate=max(1, len("check deploy status") // 4),
                pinned=True,
            ),
        ]

        def estimate_tokens(text: str) -> int:
            return max(1, len(text) // 4)

        result_segments, log, warnings = apply_trim_ladder(
            segments,
            total_cap=500,  # generous budget
            bucket_caps={"retrieval": 200, "turn_input": 200},
            decision_log=PackingDecisionLog(),
            warnings=[],
            estimate_tokens=estimate_tokens,
        )

        skill_seg = next((s for s in result_segments if "skill" in s.id), None)
        assert skill_seg is not None
        assert skill_seg.content.strip()  # not emptied

    def test_skill_segment_trimmed_when_over_budget(self) -> None:
        segments = [
            ContextSegment(
                id="retrieval:skill:deploy_checker",
                bucket="retrieval",
                content="[SKILL SNIPPET]\n" + "x" * 200,  # large snippet
                token_estimate=max(1, len("[SKILL SNIPPET]\n" + "x" * 200) // 4),
                pinned=False,
            ),
            ContextSegment(
                id="turn_input:user",
                bucket="turn_input",
                content="check deploy status",
                token_estimate=max(1, len("check deploy status") // 4),
                pinned=True,
            ),
        ]

        def estimate_tokens(text: str) -> int:
            return max(1, len(text) // 4)

        result_segments, log, warnings = apply_trim_ladder(
            segments,
            total_cap=20,  # very tight budget
            bucket_caps={"retrieval": 10, "turn_input": 100},
            decision_log=PackingDecisionLog(),
            warnings=[],
            estimate_tokens=estimate_tokens,
        )

        skill_seg = next((s for s in result_segments if "skill" in s.id), None)
        assert skill_seg is not None
        assert not skill_seg.content.strip()

    def test_skill_segment_trimmed_before_summaries(self) -> None:
        segments = [
            ContextSegment(
                id="retrieval:skill:deploy_checker",
                bucket="retrieval",
                content="[SKILL SNIPPET]\n" + "skill content " * 20,
                token_estimate=max(
                    1, len("[SKILL SNIPPET]\n" + "skill content " * 20) // 4
                ),
                pinned=False,
            ),
            ContextSegment(
                id="summaries:session",
                bucket="summaries",
                content="Session summary " * 20,
                token_estimate=max(1, len("Session summary " * 20) // 4),
                pinned=False,
            ),
            ContextSegment(
                id="turn_input:user",
                bucket="turn_input",
                content="check deploy",
                token_estimate=max(1, len("check deploy") // 4),
                pinned=True,
            ),
        ]

        def estimate_tokens(text: str) -> int:
            return max(1, len(text) // 4)

        total_before = sum(estimate_tokens(s.content) for s in segments)

        result_segments, log, warnings = apply_trim_ladder(
            segments,
            total_cap=total_before // 2,  # force trimming
            bucket_caps={
                "retrieval": 500,
                "summaries": 500,
                "turn_input": 500,
            },
            decision_log=PackingDecisionLog(),
            warnings=[],
            estimate_tokens=estimate_tokens,
        )

        skill_seg = next(s for s in result_segments if "skill" in s.id)
        summary_seg = next(s for s in result_segments if "summaries" in s.id)
        trim_buckets = [
            action.bucket
            for action in log.actions
            if action.reason_code == "over_budget"
        ]

        if trim_buckets:
            assert trim_buckets[0] == "retrieval"
        if not summary_seg.content.strip():
            assert not skill_seg.content.strip()
