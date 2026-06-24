from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.memory.storage.audit import InMemoryMemoryAuditSink
from openminion.services.agent.memory.sleep_runner import (
    SleepRunner,
    SleepRunnerConfig,
)


@dataclass
class _Cand:
    record_id: str
    utility: float
    is_promotable: bool


class _Source:
    def __init__(self, candidates):
        self.candidates = list(candidates)

    def iter_candidates(self):
        return list(self.candidates)


class _Promoter:
    def __init__(self):
        self.promoted = []

    def promote(self, candidate):
        self.promoted.append(candidate.record_id)
        return True


class _Pruner:
    def __init__(self):
        self.pruned = []

    def prune(self, candidate):
        self.pruned.append(candidate.record_id)
        return True


class _Deduper:
    def __init__(self, count):
        self.count = count

    def dedupe(self, candidates):
        return self.count


def _seed_readiness_curve(n: int) -> list[_Cand]:

    candidates = []
    for i in range(n):
        utility = i / (n - 1) if n > 1 else 0.5
        is_promotable = utility >= 0.6
        candidates.append(
            _Cand(record_id=f"r{i}", utility=utility, is_promotable=is_promotable)
        )
    return candidates


def test_integration_loop_promotes_prunes_and_audits_with_expected_curve():

    n = 200
    candidates = _seed_readiness_curve(n)
    promoter = _Promoter()
    pruner = _Pruner()
    sink = InMemoryMemoryAuditSink()
    config = SleepRunnerConfig(
        enabled=True,
        max_candidates_per_run=n,
        prune_utility_threshold=0.2,
    )
    runner = SleepRunner(
        config=config,
        candidate_source=_Source(candidates),
        promoter=promoter,
        pruner=pruner,
        deduper=_Deduper(count=5),
        audit_sink=sink,
    )

    result = runner.run_once()

    expected_pruned = sum(1 for c in candidates if c.utility < 0.2)
    expected_promoted = sum(
        1 for c in candidates if c.utility >= 0.2 and c.is_promotable
    )

    assert result.pruned == expected_pruned
    assert result.promoted == expected_promoted
    assert result.deduped == 5
    assert result.runtime_ms >= 0
    assert not result.errors

    # Audit emission
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_type == "sleep_consolidation"
    assert event.details["promoted"] == expected_promoted
    assert event.details["pruned"] == expected_pruned
    assert event.details["deduped"] == 5


def test_integration_loop_idempotent_with_no_new_candidates():

    sink = InMemoryMemoryAuditSink()
    runner = SleepRunner(
        config=SleepRunnerConfig(enabled=True),
        candidate_source=_Source([]),
        promoter=_Promoter(),
        pruner=_Pruner(),
        deduper=_Deduper(count=0),
        audit_sink=sink,
    )
    runner.run_once()
    runner.run_once()
    # Both runs emit audit (empty payload + audit row each)
    assert len(sink.events) == 2
    for ev in sink.events:
        assert ev.details["promoted"] == 0
        assert ev.details["pruned"] == 0
