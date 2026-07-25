"""Skill config default-value tests."""

from __future__ import annotations

from openminion.modules.skill.config import SkillConfig


def test_selection_rag_defaults_are_conservative() -> None:
    cfg = SkillConfig()
    assert cfg.selection_rag_threshold == 10
    assert cfg.selection_rag_topk == 5


def test_promotion_cadence_defaults_are_opt_in() -> None:
    cfg = SkillConfig()
    assert cfg.promotion_cadence_enabled is False
    assert cfg.promotion_cadence_success_threshold == 3
    assert cfg.promotion_cadence_utility_threshold == 0.7


def test_promotion_cadence_threshold_field_types_are_stable() -> None:
    cfg = SkillConfig()
    assert isinstance(cfg.promotion_cadence_success_threshold, int)
    assert isinstance(cfg.promotion_cadence_utility_threshold, float)
