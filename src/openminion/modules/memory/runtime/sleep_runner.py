import time
from dataclasses import dataclass, field
from typing import Any, Protocol

EVENT_TYPE_SLEEP_CONSOLIDATION = "sleep_consolidation"

CRON_PAYLOAD_KIND_SYSTEM_EVENT = "systemEvent"
CRON_EVENT_TEXT_SLEEP_RUN_ONCE = "sleep-consolidation:run_once"


@dataclass(frozen=True)
class SleepRunnerConfig:
    enabled: bool = False
    interval_seconds: int = 300
    max_candidates_per_run: int = 100
    prune_utility_threshold: float = 0.2

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.interval_seconds < 1:
            raise ValueError("interval_seconds must be >= 1")  # allow-bare-raise: dataclass config validation guard
        if self.max_candidates_per_run < 1:
            raise ValueError("max_candidates_per_run must be >= 1")  # allow-bare-raise: dataclass config validation guard
        if not 0.0 <= self.prune_utility_threshold <= 1.0:
            raise ValueError("prune_utility_threshold must be in [0, 1]")  # allow-bare-raise: dataclass config validation guard


@dataclass(frozen=True)
class SleepRunResult:
    promoted: int
    pruned: int
    deduped: int
    runtime_ms: int
    errors: tuple[str, ...] = field(default_factory=tuple)
    budget_exhausted: bool = False


class _AuditSink(Protocol):
    def append_event(self, event: Any) -> None:  # pragma: no cover - structural
        ...


class _CandidateSource(Protocol):
    def iter_candidates(self) -> list[Any]:  # pragma: no cover - structural
        ...


class _Promoter(Protocol):
    def promote(self, candidate: Any) -> bool:  # pragma: no cover - structural
        ...


class _Pruner(Protocol):
    def prune(self, candidate: Any) -> bool:  # pragma: no cover - structural
        ...


class _Deduper(Protocol):
    def dedupe(self, candidates: list[Any]) -> int:  # pragma: no cover - structural
        ...


@dataclass
class SleepRunner:
    """Thin orchestrator over shipped consolidation primitives."""

    config: SleepRunnerConfig
    candidate_source: _CandidateSource
    promoter: _Promoter
    pruner: _Pruner
    deduper: _Deduper
    audit_sink: _AuditSink | None = None

    def run_once(self) -> SleepRunResult:
        if not self.config.enabled:
            return SleepRunResult(promoted=0, pruned=0, deduped=0, runtime_ms=0)

        start = time.monotonic()
        promoted = 0
        pruned = 0
        errors: list[str] = []
        budget_exhausted = False

        candidates = list(self.candidate_source.iter_candidates())
        # Dedupe runs across the full candidate list first (cheap).
        try:
            deduped = int(self.deduper.dedupe(candidates))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"dedupe:{exc}")
            deduped = 0

        budget = max(1, int(self.config.max_candidates_per_run))
        budget_exhausted = len(candidates) > budget
        for candidate in candidates[:budget]:
            try:
                utility = float(getattr(candidate, "utility", 0.0) or 0.0)
                is_promotable = bool(getattr(candidate, "is_promotable", False))
            except (TypeError, ValueError):
                errors.append("candidate_field_read_error")
                continue
            if utility < float(self.config.prune_utility_threshold):
                try:
                    if self.pruner.prune(candidate):
                        pruned += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"prune:{exc}")
                continue
            if is_promotable:
                try:
                    if self.promoter.promote(candidate):
                        promoted += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"promote:{exc}")

        runtime_ms = int((time.monotonic() - start) * 1000)
        result = SleepRunResult(
            promoted=promoted,
            pruned=pruned,
            deduped=deduped,
            runtime_ms=runtime_ms,
            errors=tuple(errors),
            budget_exhausted=budget_exhausted,
        )
        self._emit_audit(result)
        return result

    def run_forever(self, *, max_iterations: int | None = None) -> int:
        """Run repeatedly with `config.interval_seconds` cadence."""

        if not self.config.enabled:
            return 0
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            self.run_once()
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                time.sleep(self.config.interval_seconds)
        return iterations

    def _emit_audit(self, result: SleepRunResult) -> None:
        if self.audit_sink is None:
            return
        try:
            from openminion.modules.memory.storage.audit import MemoryAuditEvent

            event = MemoryAuditEvent(
                event_type=EVENT_TYPE_SLEEP_CONSOLIDATION,
                target_kind="batch",
                target_id="",
                scope="",
                record_type="",
                record_key="",
                session_id="",
                details={
                    "promoted": result.promoted,
                    "pruned": result.pruned,
                    "deduped": result.deduped,
                    "runtime_ms": result.runtime_ms,
                    "errors": list(result.errors),
                    "budget_exhausted": result.budget_exhausted,
                },
            )
            self.audit_sink.append_event(event)
        except Exception:
            pass


def build_cron_payload(config: SleepRunnerConfig) -> dict[str, Any]:
    return {
        "kind": CRON_PAYLOAD_KIND_SYSTEM_EVENT,
        "session_target": "main",
        "event_text": CRON_EVENT_TEXT_SLEEP_RUN_ONCE,
        "metadata": {
            "max_candidates_per_run": int(config.max_candidates_per_run),
            "prune_utility_threshold": float(config.prune_utility_threshold),
        },
    }


__all__ = [
    "CRON_EVENT_TEXT_SLEEP_RUN_ONCE",
    "CRON_PAYLOAD_KIND_SYSTEM_EVENT",
    "EVENT_TYPE_SLEEP_CONSOLIDATION",
    "SleepRunResult",
    "SleepRunner",
    "SleepRunnerConfig",
    "build_cron_payload",
]
