"""Smoke the memory sleep runner with in-memory dependencies."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from openminion.modules.memory.storage.audit import InMemoryMemoryAuditSink
from openminion.services.agent.memory.sleep_runner import (
    SleepRunner,
    SleepRunnerConfig,
)


@dataclass
class _Candidate:
    record_id: str
    utility: float
    is_promotable: bool


class _Source:
    def __init__(self, candidates):
        self.candidates = candidates

    def iter_candidates(self):
        return list(self.candidates)


class _AcceptAll:
    def promote(self, candidate):
        return True

    def prune(self, candidate):
        return True


class _NoopDeduper:
    def dedupe(self, candidates):
        return 0


def main() -> int:
    candidates = [
        _Candidate(
            record_id=f"smoke-{i}",
            utility=0.1 if i % 3 == 0 else 0.9,
            is_promotable=True,
        )
        for i in range(30)
    ]
    accept_all = _AcceptAll()
    runner = SleepRunner(
        config=SleepRunnerConfig(enabled=True, max_candidates_per_run=50),
        candidate_source=_Source(candidates),
        promoter=accept_all,
        pruner=accept_all,
        deduper=_NoopDeduper(),
        audit_sink=InMemoryMemoryAuditSink(),
    )
    result = runner.run_once()
    print(
        f"(promoted={result.promoted}, pruned={result.pruned}, "
        f"deduped={result.deduped}, runtime_ms={result.runtime_ms})"
    )
    return 1 if result.errors else 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    sys.exit(main())
