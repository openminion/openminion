from __future__ import annotations

from dataclasses import dataclass

import pytest

from openminion.modules.memory.storage.audit import (
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.runtime.sleep_runner import (
    CRON_EVENT_TEXT_SLEEP_RUN_ONCE,
    CRON_PAYLOAD_KIND_SYSTEM_EVENT,
    EVENT_TYPE_SLEEP_CONSOLIDATION,
    SleepRunner,
    SleepRunnerConfig,
    build_cron_payload,
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
    def __init__(self, count=0):
        self.count = count

    def dedupe(self, candidates):
        return self.count


def _make_runner(config=None, candidates=None, deduper_count=0):
    config = config or SleepRunnerConfig(enabled=True)
    candidates = candidates or []
    source = _Source(candidates)
    return SleepRunner(
        config=config,
        candidate_source=source,
        promoter=_Promoter(),
        pruner=_Pruner(),
        deduper=_Deduper(count=deduper_count),
        audit_sink=InMemoryMemoryAuditSink(),
    )


def test_disabled_runner_is_strict_noop():
    runner = _make_runner(config=SleepRunnerConfig(enabled=False))
    result = runner.run_once()
    assert result.promoted == 0
    assert result.pruned == 0
    assert result.deduped == 0
    assert result.runtime_ms == 0


def test_enabled_runner_promotes_high_utility_candidates():
    candidates = [
        _Cand(record_id="A", utility=0.9, is_promotable=True),
        _Cand(record_id="B", utility=0.85, is_promotable=True),
    ]
    runner = _make_runner(candidates=candidates)
    result = runner.run_once()
    assert result.promoted == 2
    assert result.pruned == 0


def test_enabled_runner_prunes_below_threshold():
    candidates = [
        _Cand(record_id="L1", utility=0.05, is_promotable=False),
        _Cand(record_id="L2", utility=0.1, is_promotable=False),
    ]
    runner = _make_runner(
        config=SleepRunnerConfig(enabled=True, prune_utility_threshold=0.2),
        candidates=candidates,
    )
    result = runner.run_once()
    assert result.pruned == 2
    assert result.promoted == 0


def test_runner_records_deduper_count():
    runner = _make_runner(
        candidates=[_Cand(record_id="A", utility=0.9, is_promotable=True)],
        deduper_count=4,
    )
    result = runner.run_once()
    assert result.deduped == 4


def test_runner_budget_yields_when_exhausted():
    candidates = [
        _Cand(record_id=f"r{i}", utility=0.9, is_promotable=True) for i in range(5)
    ]
    runner = _make_runner(
        config=SleepRunnerConfig(enabled=True, max_candidates_per_run=2),
        candidates=candidates,
    )
    result = runner.run_once()
    assert result.promoted == 2
    assert result.budget_exhausted is True


def test_runner_idempotent_when_promoter_drains_state():
    runner = _make_runner(candidates=[])
    a = runner.run_once()
    b = runner.run_once()
    assert (a.promoted, a.pruned, a.deduped) == (0, 0, 0)
    assert (b.promoted, b.pruned, b.deduped) == (0, 0, 0)


def test_default_config_is_disabled():
    cfg = SleepRunnerConfig()
    assert cfg.enabled is False
    assert cfg.interval_seconds == 300
    assert cfg.max_candidates_per_run == 100
    assert cfg.prune_utility_threshold == 0.2


def test_config_validates_thresholds():
    with pytest.raises(ValueError):
        SleepRunnerConfig(interval_seconds=0)
    with pytest.raises(ValueError):
        SleepRunnerConfig(max_candidates_per_run=0)
    with pytest.raises(ValueError):
        SleepRunnerConfig(prune_utility_threshold=1.5)


def test_build_cron_payload_uses_systemEvent_kind_not_agent_idle_tick():
    payload = build_cron_payload(SleepRunnerConfig(enabled=True))
    assert payload["kind"] == CRON_PAYLOAD_KIND_SYSTEM_EVENT
    assert payload["kind"] != "agentIdleTick"
    assert payload["session_target"] == "main"
    assert payload["event_text"] == CRON_EVENT_TEXT_SLEEP_RUN_ONCE


def test_cron_payload_carries_budget_metadata():
    payload = build_cron_payload(
        SleepRunnerConfig(
            enabled=True, max_candidates_per_run=42, prune_utility_threshold=0.5
        )
    )
    assert payload["metadata"]["max_candidates_per_run"] == 42
    assert payload["metadata"]["prune_utility_threshold"] == 0.5


def test_runner_emits_audit_event_on_each_run():
    sink = InMemoryMemoryAuditSink()
    candidates = [
        _Cand(record_id="A", utility=0.9, is_promotable=True),
    ]
    runner = SleepRunner(
        config=SleepRunnerConfig(enabled=True),
        candidate_source=_Source(candidates),
        promoter=_Promoter(),
        pruner=_Pruner(),
        deduper=_Deduper(count=1),
        audit_sink=sink,
    )
    runner.run_once()
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_type == EVENT_TYPE_SLEEP_CONSOLIDATION
    assert event.target_kind == "batch"
    assert event.details["promoted"] == 1
    assert event.details["deduped"] == 1


def test_runner_emits_audit_event_with_errors_recorded():
    class _RaisingPromoter:
        def promote(self, candidate):
            raise RuntimeError("boom")

    candidates = [_Cand(record_id="A", utility=0.9, is_promotable=True)]
    sink = InMemoryMemoryAuditSink()
    runner = SleepRunner(
        config=SleepRunnerConfig(enabled=True),
        candidate_source=_Source(candidates),
        promoter=_RaisingPromoter(),
        pruner=_Pruner(),
        deduper=_Deduper(),
        audit_sink=sink,
    )
    result = runner.run_once()
    assert any("promote:" in e for e in result.errors)
    assert len(sink.events) == 1
