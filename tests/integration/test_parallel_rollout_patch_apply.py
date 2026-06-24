from __future__ import annotations

import asyncio
from pathlib import Path

from openminion.modules.brain.loop.rollouts import (
    RolloutPlan,
    StubRolloutScorer,
    WorktreeIsolator,
    parallel_rollout,
)


def test_integration_three_patches_isolated_winner_selected_no_leaks():

    isolator = WorktreeIsolator(run_id="integration-test")
    worktrees = isolator.allocate(3)
    try:
        # Each rollout writes a different patch into its own worktree.
        async def action(index, state):
            worktree = worktrees[index]
            patch_file = worktree / "patch.txt"
            patch_file.write_text(f"rollout-{index}-patch\n", encoding="utf-8")
            return {
                "worktree": str(worktree),
                "patch_path": str(patch_file),
                "patch_content": patch_file.read_text(encoding="utf-8"),
            }

        # Scorer: prefer rollout 1 (highest deterministic score).
        def score_fn(result, plan):
            content = result.output["patch_content"]
            return {
                "rollout-0-patch\n": 0.4,
                "rollout-1-patch\n": 0.9,
                "rollout-2-patch\n": 0.6,
            }.get(content, 0.0)

        plan = RolloutPlan(
            step_id="patch_apply",
            n_rollouts=3,
            max_parallelism=3,
            isolation_kind="worktree",
            scorer_id="stub",
            timeout_seconds=10,
        )
        winner = parallel_rollout(
            plan,
            action=action,
            state={},
            scorer=StubRolloutScorer(scorer_fn=score_fn),
        )
        # Winning rollout is rollout-1 (highest score).
        assert winner.succeeded
        assert winner.output["patch_content"] == "rollout-1-patch\n"

        # Cross-worktree isolation: each worktree only contains its own patch.
        for i, w in enumerate(worktrees):
            files = sorted(p.name for p in Path(w).iterdir())
            assert files == ["patch.txt"]
            assert Path(w, "patch.txt").read_text() == f"rollout-{i}-patch\n"
    finally:
        isolator.release()

    # Leak check: zero leftover worktrees on disk.
    assert all(not Path(w).exists() for w in worktrees)
    # Detector confirms no leaks.
    isolator.assert_no_leaks()


def test_integration_partial_failure_still_returns_best_succeeded():
    isolator = WorktreeIsolator()
    worktrees = isolator.allocate(3)
    try:

        async def action(index, state):
            if index == 0:
                raise RuntimeError("rollout 0 crashed")
            (worktrees[index] / "out.txt").write_text(str(index), encoding="utf-8")
            return {"value": index}

        plan = RolloutPlan(
            step_id="patch_apply",
            n_rollouts=3,
            max_parallelism=3,
            isolation_kind="worktree",
            scorer_id="stub",
            timeout_seconds=10,
        )
        winner = parallel_rollout(
            plan,
            action=action,
            state={},
            scorer=StubRolloutScorer(
                scorer_fn=lambda r, p: float(r.output["value"]) / 3.0
            ),
        )
        assert winner.succeeded
        assert winner.output["value"] == 2
    finally:
        isolator.release()
    assert all(not Path(w).exists() for w in worktrees)


def test_integration_async_smoke_runs_without_thread_pool_leak():

    from openminion.modules.brain.loop.rollouts.runner import (
        parallel_rollout_async,
    )

    isolator = WorktreeIsolator()
    worktrees = isolator.allocate(2)
    try:

        async def action(index, state):
            (worktrees[index] / "x.txt").write_text(str(index), encoding="utf-8")
            return {"value": index}

        plan = RolloutPlan(
            step_id="patch_apply",
            n_rollouts=2,
            max_parallelism=2,
            isolation_kind="worktree",
            scorer_id="stub",
            timeout_seconds=5,
        )

        winner = asyncio.run(
            parallel_rollout_async(
                plan,
                action=action,
                state={},
                scorer=StubRolloutScorer(scorer_fn=lambda r, p: 0.7),
            )
        )
        assert winner.succeeded
    finally:
        isolator.release()
    assert all(not Path(w).exists() for w in worktrees)
