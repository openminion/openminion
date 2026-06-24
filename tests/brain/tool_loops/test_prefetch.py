from __future__ import annotations

from openminion.modules.brain.loop.tools.prefetch import PrefetchPredictor


def test_correct_prediction_increments_correct() -> None:
    predictor = PrefetchPredictor()
    predictor.observe(["tool_a", "tool_b", "tool_c"])
    predictor.observe(["tool_a", "tool_b", "tool_c"])

    predicted, conf = predictor.predict(["tool_a", "tool_b"])
    assert predicted == "tool_c"
    assert conf == 1.0

    predictor.record_outcome(predicted, "tool_c")
    assert predictor.correct == 1
    assert predictor.wrong == 0


def test_wrong_prediction_increments_wrong() -> None:
    predictor = PrefetchPredictor()
    predictor.observe(["tool_a", "tool_b", "tool_c"])
    predictor.observe(["tool_a", "tool_b", "tool_c"])

    predicted, conf = predictor.predict(["tool_a", "tool_b"])
    assert predicted == "tool_c"

    predictor.record_outcome(predicted, "tool_x")
    assert predictor.correct == 0
    assert predictor.wrong == 1


def test_predictor_not_created_when_disabled() -> None:
    from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopProfile

    profile = AdaptiveToolLoopProfile(
        profile_name="test",
        mode_name="act",
        tool_exposure_policy="explicit_allowlist",
        allowed_tools=frozenset({"tool_a"}),
    )
    assert profile.speculative_prefetch is False


def test_confidence_threshold_gates_prefetch() -> None:
    predictor = PrefetchPredictor()
    predictor.observe(["tool_a", "tool_b", "tool_c"])
    predictor.observe(["tool_a", "tool_b", "tool_d"])

    predicted, conf = predictor.predict(["tool_a", "tool_b"])
    assert predicted is not None
    assert conf == 0.5
    threshold = 0.8
    should_prefetch = conf >= threshold
    assert not should_prefetch


def test_predictor_learns_from_repeated_patterns() -> None:
    predictor = PrefetchPredictor()
    for _ in range(5):
        predictor.observe(["read", "write", "commit"])

    predicted, conf = predictor.predict(["read", "write"])
    assert predicted == "commit"
    assert conf == 1.0


def test_no_prediction_for_unseen_context() -> None:
    predictor = PrefetchPredictor()
    predictor.observe(["tool_a", "tool_b"])

    predicted, conf = predictor.predict(["tool_x", "tool_y"])
    assert predicted is None
    assert conf == 0.0


def test_empty_recent_tools_returns_no_prediction() -> None:
    predictor = PrefetchPredictor()
    predictor.observe(["tool_a", "tool_b"])

    predicted, conf = predictor.predict([])
    assert predicted is None
    assert conf == 0.0
