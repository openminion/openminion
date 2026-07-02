from __future__ import annotations

import pytest

from openminion.modules.brain.loop.tools.reflection import (
    AnomalyScore,
    detect_anomaly,
)
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopProfile,
    profile_include_reflect,
)


class _FakeResult:
    def __init__(self, *, status="ok", error=None, summary="result", outputs=None):
        self.status = status
        self.error = error
        self.summary = summary
        self.outputs = outputs


class TestDetectAnomaly:
    def test_error_status_triggers_score_1(self):
        result = _FakeResult(status="error", error=object())
        score = detect_anomaly(result=result, history=[])
        assert score.score == 1.0
        assert "error_status" in score.triggered_conditions

    def test_empty_unexpected_triggers_score_07(self):
        prior = _FakeResult(summary="some content")
        result = _FakeResult(summary="", outputs=None)
        score = detect_anomaly(result=result, history=[prior])
        assert score.score == 0.7
        assert "empty_unexpected" in score.triggered_conditions

    def test_double_failure_triggers_score_09(self):
        prior = _FakeResult(status="error", error=object())
        result = _FakeResult(status="error", error=object())
        score = detect_anomaly(result=result, history=[prior])
        assert score.score == 1.0  # max of error_status(1.0) and double_failure(0.9)
        assert "double_failure" in score.triggered_conditions

    def test_size_deviation_triggers_score_05(self):
        prior1 = _FakeResult(summary="x" * 100)
        prior2 = _FakeResult(summary="x" * 120)
        result = _FakeResult(summary="x" * 1000)  # >3x mean
        score = detect_anomaly(result=result, history=[prior1, prior2])
        assert score.score == 0.5
        assert "size_deviation" in score.triggered_conditions

    def test_normal_result_score_0(self):
        result = _FakeResult(summary="normal output")
        score = detect_anomaly(result=result, history=[])
        assert score.score == 0.0
        assert score.triggered_conditions == ()

    def test_multiple_conditions_max_score(self):
        prior = _FakeResult(status="error", error=object(), summary="content")
        result = _FakeResult(status="error", error=object(), summary="")
        score = detect_anomaly(result=result, history=[prior])
        assert score.score == 1.0
        assert len(score.triggered_conditions) >= 2

    def test_first_call_empty_no_anomaly(self):
        result = _FakeResult(summary="", outputs=None)
        score = detect_anomaly(result=result, history=[])
        # No prior non-empty results, so empty_unexpected should NOT trigger
        assert "empty_unexpected" not in score.triggered_conditions

    def test_failed_status_triggers_error_condition(self):
        result = _FakeResult(status="failed")
        score = detect_anomaly(result=result, history=[])
        assert score.score == 1.0
        assert "error_status" in score.triggered_conditions

    def test_size_deviation_not_triggered_with_only_one_prior(self):
        prior = _FakeResult(summary="x" * 100)
        result = _FakeResult(summary="x" * 1000)
        score = detect_anomaly(result=result, history=[prior])
        # Only one prior non-empty result — not enough for size deviation check
        assert "size_deviation" not in score.triggered_conditions

    def test_anomaly_score_is_frozen(self):
        score = AnomalyScore(score=0.5, triggered_conditions=("size_deviation",))
        with pytest.raises(Exception):
            score.score = 1.0  # type: ignore[misc]


class TestReflectionPolicy:
    def test_never_no_reflect(self):
        p = AdaptiveToolLoopProfile(
            profile_name="test",
            mode_name="test",
            reflection_policy="never",
            allowed_tools=frozenset({"file.read"}),
        )
        assert not profile_include_reflect(p)

    def test_always_reflect(self):
        p = AdaptiveToolLoopProfile(
            profile_name="test",
            mode_name="test",
            reflection_policy="always",
            allowed_tools=frozenset({"file.read"}),
        )
        assert profile_include_reflect(p)

    def test_anomaly_no_reflect(self):
        p = AdaptiveToolLoopProfile(
            profile_name="test",
            mode_name="test",
            reflection_policy="anomaly",
            allowed_tools=frozenset({"file.read"}),
        )
        assert not profile_include_reflect(p)

    def test_default_is_never(self):
        p = AdaptiveToolLoopProfile(
            profile_name="test",
            mode_name="test",
            allowed_tools=frozenset({"file.read"}),
        )
        assert p.reflection_policy == "never"
        assert not profile_include_reflect(p)
