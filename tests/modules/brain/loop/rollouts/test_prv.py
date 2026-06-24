from __future__ import annotations

import asyncio

import pytest

from openminion.modules.brain.loop.rollouts import (
    HighestScoreSelector,
    LLMRolloutScorer,
    PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS,
    ParallelRolloutConfig,
    RolloutPlan,
    RolloutResult,
    StubRolloutScorer,
    WorktreeIsolator,
    is_step_eligible_for_parallel_rollout,
    parallel_rollout,
)
from openminion.modules.brain.loop.rollouts.runner import (
    parallel_rollout_async,
)


def _make_plan(**overrides):
    payload = dict(
        step_id="s1",
        n_rollouts=3,
        max_parallelism=3,
        isolation_kind="worktree",
        scorer_id="stub",
        timeout_seconds=5,
    )
    payload.update(overrides)
    return RolloutPlan(**payload)


# --- PRV-01 contracts ---


def test_rollout_plan_validates_positive_fields():
    with pytest.raises(ValueError):
        RolloutPlan(
            step_id="s",
            n_rollouts=0,
            max_parallelism=1,
            isolation_kind="worktree",
            scorer_id="x",
            timeout_seconds=1,
        )


def test_rollout_plan_is_frozen():
    plan = _make_plan()
    with pytest.raises(Exception):
        plan.n_rollouts = 9  # type: ignore[misc]


def test_rollout_result_succeeded_flag_reads_error():
    ok = RolloutResult(rollout_id="a", output={}, quality_score=0.5)
    bad = RolloutResult(rollout_id="b", output=None, error="boom")
    assert ok.succeeded is True
    assert bad.succeeded is False


# --- PRV-02 isolator ---


def test_isolator_allocates_n_temp_dirs_and_releases():
    isolator = WorktreeIsolator()
    dirs = isolator.allocate(3)
    assert len(dirs) == 3
    assert all(d.is_dir() for d in dirs)
    isolator.release()
    assert all(not d.exists() for d in dirs)


def test_isolator_worktrees_context_manager_cleans_up_on_exception():
    isolator = WorktreeIsolator()
    captured: list = []
    try:
        with isolator.worktrees(2) as dirs:
            captured = list(dirs)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert all(not d.exists() for d in captured)


def test_isolator_assert_no_leaks_passes_after_release():
    isolator = WorktreeIsolator()
    isolator.allocate(2)
    isolator.release()
    isolator.assert_no_leaks()


def test_isolator_assert_no_leaks_raises_when_dirs_remain():

    isolator = WorktreeIsolator()
    d = isolator.allocate(1)[0]
    assert d.exists()
    # Do NOT release; detector should trip.
    with pytest.raises(RuntimeError):
        isolator.assert_no_leaks()
    isolator.release()


# --- PRV-03 scorer + runner ---


def test_stub_scorer_clamps_to_unit_interval():
    scorer = StubRolloutScorer(scorer_fn=lambda r, p: 2.5)
    plan = _make_plan()
    r = RolloutResult(rollout_id="a", output={})
    assert scorer.score(r, plan) == 1.0
    scorer2 = StubRolloutScorer(scorer_fn=lambda r, p: -0.5)
    assert scorer2.score(r, plan) == 0.0


def test_llm_scorer_returns_zero_on_client_failure():
    class _BadClient:
        def call(self, *, prompt, timeout_seconds):
            raise RuntimeError("model down")

    scorer = LLMRolloutScorer(client=_BadClient())
    plan = _make_plan()
    r = RolloutResult(rollout_id="a", output={})
    assert scorer.score(r, plan) == 0.0


def test_llm_scorer_normalizes_invalid_response_to_zero():
    class _BadClient:
        def call(self, *, prompt, timeout_seconds):
            return {"quality_score": "not_a_number"}

    scorer = LLMRolloutScorer(client=_BadClient())
    plan = _make_plan()
    r = RolloutResult(rollout_id="a", output={})
    assert scorer.score(r, plan) == 0.0


def test_parallel_rollout_picks_highest_score():
    plan = _make_plan(n_rollouts=3)

    async def action(index, state):
        return {"value": index}

    # Score by index — rollout 2 wins.
    scorer = StubRolloutScorer(scorer_fn=lambda r, p: float(r.output["value"]) / 3.0)
    winner = parallel_rollout(plan, action=action, state={}, scorer=scorer)
    assert winner.output["value"] == 2
    assert winner.quality_score == pytest.approx(2 / 3)


def test_parallel_rollout_handles_partial_failures():
    plan = _make_plan(n_rollouts=3)

    async def action(index, state):
        if index == 1:
            raise RuntimeError(f"rollout {index} failed")
        return {"value": index}

    scorer = StubRolloutScorer(scorer_fn=lambda r, p: float(r.output["value"]) / 3.0)
    winner = parallel_rollout(plan, action=action, state={}, scorer=scorer)
    # Winning rollout is one of the succeeded ones (0 or 2).
    assert winner.succeeded
    assert winner.output["value"] in (0, 2)


def test_parallel_rollout_all_fail_returns_error_result():
    plan = _make_plan(n_rollouts=2)

    async def action(index, state):
        raise RuntimeError("nope")

    scorer = StubRolloutScorer(scorer_fn=lambda r, p: 0.0)
    winner = parallel_rollout(plan, action=action, state={}, scorer=scorer)
    assert winner.succeeded is False


def test_parallel_rollout_timeout_returns_typed_failure():
    plan = _make_plan(n_rollouts=2, timeout_seconds=1)

    async def slow_action(index, state):
        await asyncio.sleep(5)
        return {"value": index}

    scorer = StubRolloutScorer(scorer_fn=lambda r, p: 1.0)
    winner = parallel_rollout(plan, action=slow_action, state={}, scorer=scorer)
    # All timed out → synthesized failure with error string
    assert winner.error == "all_rollouts_timed_out"


def test_highest_score_selector_breaks_ties_by_latency():
    results = [
        RolloutResult(rollout_id="a", output={}, quality_score=0.8, latency_ms=100),
        RolloutResult(rollout_id="b", output={}, quality_score=0.8, latency_ms=50),
        RolloutResult(rollout_id="c", output={}, quality_score=0.7, latency_ms=10),
    ]
    winner = HighestScoreSelector().select(results)
    assert winner.rollout_id == "b"


def test_highest_score_selector_prefers_succeeded_when_present():
    results = [
        RolloutResult(rollout_id="ok", output={}, quality_score=0.5),
        RolloutResult(rollout_id="bad", output=None, quality_score=0.9, error="x"),
    ]
    winner = HighestScoreSelector().select(results)
    assert winner.rollout_id == "ok"


def test_highest_score_selector_empty_list_raises():
    with pytest.raises(ValueError):
        HighestScoreSelector().select([])


# --- PRV-04 strategy gate ---


def test_strategy_gate_admits_known_step_kinds():
    for kind in ("patch_apply", "structured_json_emit", "test_authoring"):
        assert kind in PARALLEL_ROLLOUT_ELIGIBLE_STEP_KINDS


def test_strategy_gate_rejects_conversational_steps_negative_path():

    for kind in ("conversation", "planning", "analysis", "respond", ""):
        assert is_step_eligible_for_parallel_rollout(kind) is False


def test_strategy_gate_default_static_allowlist_admits_when_no_operator_list():
    assert is_step_eligible_for_parallel_rollout("patch_apply") is True


def test_strategy_gate_with_empty_operator_allowlist_rejects():

    assert (
        is_step_eligible_for_parallel_rollout("patch_apply", operator_allowlist=[])
        is False
    )


def test_strategy_gate_with_partial_operator_allowlist():
    assert (
        is_step_eligible_for_parallel_rollout(
            "patch_apply", operator_allowlist=["patch_apply"]
        )
        is True
    )
    assert (
        is_step_eligible_for_parallel_rollout(
            "structured_json_emit", operator_allowlist=["patch_apply"]
        )
        is False
    )


# --- PRV-05 config ---


def test_default_config_is_disabled_with_empty_allowlist():
    cfg = ParallelRolloutConfig()
    assert cfg.enabled is False
    assert cfg.n_rollouts == 3
    assert cfg.eligible_step_kinds == ()


def test_config_validates_positive_fields():
    with pytest.raises(ValueError):
        ParallelRolloutConfig(n_rollouts=0)


# --- Cross-seam non-collision regression ---


def test_no_cross_import_from_approval():

    import openminion.modules.brain.loop.rollouts.scorer as s
    import openminion.modules.brain.loop.rollouts.runner as r

    src = (s.__file__, r.__file__)
    for path in src:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "runtime.approval" not in text
        assert "ApprovalVerdict" not in text


# --- async-direct test exercising parallel_rollout_async ---


def test_parallel_rollout_async_smoke():
    plan = _make_plan(n_rollouts=2)

    async def action(index, state):
        return {"value": index}

    scorer = StubRolloutScorer(scorer_fn=lambda r, p: 0.5)

    winner = asyncio.run(
        parallel_rollout_async(plan, action=action, state={}, scorer=scorer)
    )
    assert winner.succeeded
