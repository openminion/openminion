"""Concurrent rollout runner and winner selection."""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from openminion.modules.llm.runtime.sync import run_async_compat
from openminion.modules.brain.loop.rollouts.interfaces import (
    RolloutScorer,
    RolloutSelector,
)
from openminion.modules.brain.loop.rollouts.schemas import (
    RolloutPlan,
    RolloutResult,
)


RolloutAction = Callable[[int, dict[str, Any]], Awaitable[Any]]


@dataclass
class HighestScoreSelector:
    """Selects the best rollout, breaking ties by latency."""

    def select(self, results: Sequence[RolloutResult]) -> RolloutResult:
        if not results:
            raise ValueError("HighestScoreSelector.select: empty results")
        succeeded = [r for r in results if r.succeeded]
        return min(
            succeeded or list(results),
            key=lambda r: (-r.quality_score, r.latency_ms),
        )


async def _run_one_rollout(
    *,
    index: int,
    plan: RolloutPlan,
    action: RolloutAction,
    state: dict[str, Any],
    scorer: RolloutScorer,
    sem: asyncio.Semaphore,
) -> RolloutResult:
    rollout_id = f"{plan.step_id}-r{index}-{uuid.uuid4().hex[:8]}"
    start = time.monotonic()
    async with sem:
        try:
            output = await action(index, state)
            latency_ms = int((time.monotonic() - start) * 1000)
            partial = RolloutResult(
                rollout_id=rollout_id,
                output=output,
                latency_ms=latency_ms,
            )
            score = scorer.score(partial, plan)
        except Exception as exc:  # noqa: BLE001
            return RolloutResult(
                rollout_id=rollout_id,
                output=None,
                quality_score=0.0,
                latency_ms=int((time.monotonic() - start) * 1000),
                error=str(exc),
            )
    return RolloutResult(
        rollout_id=rollout_id,
        output=output,
        quality_score=float(score),
        latency_ms=latency_ms,
    )


async def parallel_rollout_async(
    plan: RolloutPlan,
    *,
    action: RolloutAction,
    state: dict[str, Any],
    scorer: RolloutScorer,
    selector: RolloutSelector | None = None,
) -> RolloutResult:
    """Async entry point for spawning, scoring, and selecting rollouts."""

    sem = asyncio.Semaphore(plan.max_parallelism)
    tasks = [
        asyncio.create_task(
            _run_one_rollout(
                index=i,
                plan=plan,
                action=action,
                state=dict(state),
                scorer=scorer,
                sem=sem,
            )
        )
        for i in range(plan.n_rollouts)
    ]

    try:
        done, pending = await asyncio.wait(tasks, timeout=float(plan.timeout_seconds))
    except Exception:
        for t in tasks:
            t.cancel()
        raise

    for p in pending:
        p.cancel()

    results: list[RolloutResult] = []
    for task in done:
        try:
            results.append(task.result())
        except Exception as exc:  # noqa: BLE001
            results.append(
                RolloutResult(
                    rollout_id=f"{plan.step_id}-error",
                    output=None,
                    error=str(exc),
                )
            )

    if not results:
        # All rollouts timed out — synthesize a typed failure.
        return RolloutResult(
            rollout_id=f"{plan.step_id}-timeout",
            output=None,
            quality_score=0.0,
            latency_ms=int(plan.timeout_seconds * 1000),
            error="all_rollouts_timed_out",
        )

    return (selector or HighestScoreSelector()).select(results)


def parallel_rollout(
    plan: RolloutPlan,
    *,
    action: RolloutAction,
    state: dict[str, Any],
    scorer: RolloutScorer,
    selector: RolloutSelector | None = None,
) -> RolloutResult:
    """Sync wrapper around the async runner."""

    return run_async_compat(
        parallel_rollout_async(
            plan,
            action=action,
            state=state,
            scorer=scorer,
            selector=selector,
        )
    )
