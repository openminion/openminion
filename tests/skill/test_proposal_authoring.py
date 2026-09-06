from __future__ import annotations

import pytest

from openminion.modules.skill.proposal import (
    proposal_from_markdown,
    propose_skills_from_task_shapes,
)


SKILL_MARKDOWN = """---
name: local-system-summary
description: Gather a small local system summary.
tools:
  - host.inventory_report
tags:
  - inventory
verification:
  - Confirm report.md exists.
---
# Local System Summary

## Procedure

Gather local system facts and write report.md.
"""


def test_proposal_from_markdown_keeps_exact_content_and_derives_draft() -> None:
    proposal = proposal_from_markdown(
        SKILL_MARKDOWN,
        source_task_shape_ref="session:session-a",
        evidence_refs=["run_id:run-a", "trace_id:trace-a"],
    )

    assert proposal.skill_markdown == SKILL_MARKDOWN
    assert proposal.source_task_shape_ref == "session:session-a"
    assert proposal.evidence_refs == ["run_id:run-a", "trace_id:trace-a"]
    assert proposal.proposed_skill_definition.name == "local_system_summary"
    assert proposal.proposed_skill_definition.short_description == (
        "Gather a small local system summary."
    )
    assert proposal.proposed_skill_definition.tools == ["host.inventory_report"]


def test_proposal_from_markdown_is_deterministic() -> None:
    kwargs = {
        "source_task_shape_ref": "session:session-a",
        "evidence_refs": ["trace_id:trace-a"],
    }
    assert proposal_from_markdown(SKILL_MARKDOWN, **kwargs).proposal_id == (
        proposal_from_markdown(SKILL_MARKDOWN, **kwargs).proposal_id
    )


def test_proposal_identity_excludes_per_run_evidence() -> None:
    first = proposal_from_markdown(
        SKILL_MARKDOWN,
        source_task_shape_ref="session:one",
        evidence_refs=["run_id:first", "trace_id:first"],
    )
    second = proposal_from_markdown(
        SKILL_MARKDOWN,
        source_task_shape_ref="session:one",
        evidence_refs=["run_id:second", "trace_id:second"],
    )

    assert first.proposal_id == second.proposal_id
    assert first.evidence_refs != second.evidence_refs


@pytest.mark.parametrize(
    "markdown",
    [
        "",
        "# Missing front matter\n\nDo work.",
        "---\nname: no-description\n---\n# No description",
        "---\nname: empty\ndescription: No procedure.\n---\n",
    ],
)
def test_proposal_from_markdown_rejects_incomplete_content(markdown: str) -> None:
    with pytest.raises(ValueError):
        proposal_from_markdown(
            markdown,
            source_task_shape_ref="session:session-a",
        )


def test_shape_proposal_id_remains_stable_without_markdown() -> None:
    proposals = propose_skills_from_task_shapes(
        [
            {
                "task_shape_ref": (
                    "task_shape:research_strategy|live_information|latest_news"
                ),
                "strategy_id": "research_strategy",
                "capability_category": "live_information",
                "intent_category": "latest_news",
                "performance_entry_refs": [
                    "performance:research_strategy|live_information|latest_news"
                ],
            }
        ],
        current_catalog=[],
        policy_id="skill_promotion_cadence_v1",
    )

    assert proposals[0].proposal_id == (
        "65e5b1fe868fcac2c7d85aaab9aa345f78eac5a882b1921b5ad2ce91ddf669d1"
    )
    assert proposals[0].skill_markdown == ""
