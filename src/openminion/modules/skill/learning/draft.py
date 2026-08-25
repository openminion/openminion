"""Render reviewable SKILL.md drafts from workflow shapes."""

from __future__ import annotations

from collections.abc import Iterable

from openminion.modules.skill.models import slugify

from .shapes import WorkflowShape


class SkillDraftError(ValueError):
    """Raised when a learned-skill draft violates runtime-owned constraints."""


def _bullet_lines(items: Iterable[str]) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    return "\n".join(f"- {item}" for item in values) if values else "- none"


def render_skill_markdown(
    shape: WorkflowShape,
    *,
    title: str,
    description: str,
    steps: list[str],
    validation_rules: list[str],
    risk_notes: list[str] | None = None,
    source_changing: bool = False,
) -> str:
    """Render deterministic SKILL.md-compatible content for operator review."""

    if source_changing and not validation_rules:
        raise SkillDraftError("source_changing_workflow_requires_validation")
    if not steps:
        raise SkillDraftError("steps_required")
    name = slugify(title, fallback=shape.shape_id)
    evidence_refs = shape.evidence_refs or [shape.task_shape_ref]
    return "\n".join(
        [
            "---",
            f"name: {name}",
            f"description: {description.strip()}",
            "---",
            "",
            "# When To Use",
            "",
            _bullet_lines(
                [
                    f"intent: {shape.intent_category}",
                    f"capability: {shape.capability_category}",
                    f"strategy: {shape.strategy_id}",
                ]
            ),
            "",
            "# Do Not Use",
            "",
            _bullet_lines(
                [
                    "Do not bypass operator review or replay/eval proof.",
                    "Do not use when required tools or validation are unavailable.",
                ]
            ),
            "",
            "# Steps",
            "",
            _bullet_lines(steps),
            "",
            "# Validation",
            "",
            _bullet_lines(validation_rules),
            "",
            "# Evidence",
            "",
            _bullet_lines(evidence_refs),
            "",
            "# Risk Notes",
            "",
            _bullet_lines(risk_notes or [f"risk: {shape.risk_level}"]),
            "",
        ]
    )


__all__ = ("SkillDraftError", "render_skill_markdown")
