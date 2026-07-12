from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from openminion.modules.brain.loop.rollouts import (
    RolloutPlan,
    StubRolloutScorer,
    WorktreeIsolator,
    parallel_rollout,
)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "rollout-test@example.invalid")
    _run_git(repo, "config", "user.name", "Rollout Test")
    (repo / "seed.py").write_text("VALUE = 0\n", encoding="utf-8")
    _run_git(repo, "add", "seed.py")
    _run_git(repo, "commit", "-m", "seed")
    return repo


def test_integration_three_patches_isolated_winner_selected_no_leaks(
    tmp_path: Path,
):
    repo = _git_repo(tmp_path)
    (repo / "parent-only.txt").write_text("dirty parent\n", encoding="utf-8")

    isolator = WorktreeIsolator(parent_root=repo, run_id="integration-test")
    worktrees = isolator.allocate(3)
    try:
        # Each rollout writes a different patch into its own worktree.
        async def action(index, state):
            worktree = worktrees[index]
            patch_file = worktree / "patch.txt"
            patch_file.write_text(f"rollout-{index}-patch\n", encoding="utf-8")
            (worktree / "seed.py").write_text(
                f"VALUE = {index + 1}\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-m", "py_compile", "seed.py"],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            )
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

        # Each worktree starts from committed state and excludes dirty parent files.
        for i, w in enumerate(worktrees):
            files = sorted(p.name for p in Path(w).iterdir())
            assert files == [".git", "__pycache__", "patch.txt", "seed.py"]
            assert Path(w, "patch.txt").read_text() == f"rollout-{i}-patch\n"
            assert not Path(w, "parent-only.txt").exists()
            diff = _run_git(w, "diff", "--", "seed.py").stdout
            assert f"+VALUE = {i + 1}" in diff
    finally:
        isolator.release()

    # Leak check: zero leftover worktrees on disk.
    assert all(not Path(w).exists() for w in worktrees)
    # Detector confirms no leaks.
    isolator.assert_no_leaks()


def test_integration_partial_failure_still_returns_best_succeeded(tmp_path: Path):
    isolator = WorktreeIsolator(parent_root=_git_repo(tmp_path))
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


def test_integration_async_smoke_runs_without_thread_pool_leak(tmp_path: Path):

    from openminion.modules.brain.loop.rollouts.runner import (
        parallel_rollout_async,
    )

    isolator = WorktreeIsolator(parent_root=_git_repo(tmp_path))
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


def test_worktree_isolator_rejects_non_git_parent(tmp_path: Path):
    with pytest.raises(ValueError, match="not a Git repository"):
        WorktreeIsolator(parent_root=tmp_path)
