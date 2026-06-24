from __future__ import annotations

import math

import pytest

from openminion.modules.memory.config import RankingConfig
from openminion.modules.memory.runtime.scorer import (
    RankingWeights,
    _type_multiplier,
    recency_score,
    score_records,
)


def test_type_multiplier_defaults_without_ranking_config() -> None:
    assert _type_multiplier("correction", None) == 1.5
    assert _type_multiplier("user_preference", None) == 1.3
    assert _type_multiplier("pin", None) == 1.2
    assert _type_multiplier("project_convention", None) == 1.1
    assert _type_multiplier("meta_insight", None) == 1.05
    assert _type_multiplier("unknown", None) == 1.0


def test_type_multiplier_uses_ranking_config_values() -> None:
    ranking = RankingConfig(type_boost_correction=2.0)

    assert _type_multiplier("correction", ranking) == 2.0


def test_score_records_empty_input_returns_empty_list() -> None:
    assert score_records([]) == []


def test_ranking_weights_non_summing_values_auto_normalize() -> None:
    with pytest.warns(UserWarning, match="auto-normalizing"):
        weights = RankingWeights(relevance=2.0, recency=1.0)

    total = (
        weights.relevance
        + weights.recency
        + weights.feedback
        + weights.type_bonus
        + weights.confidence
        + weights.outcome_utility
    )
    assert math.isclose(total, 1.0)


def test_recency_score_zero_half_life_returns_one() -> None:
    assert recency_score(age_days=0, half_life_days=0) == 1.0
