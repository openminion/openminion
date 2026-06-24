from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.trust.rate_limit import PromotionRateLimiter
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter

try:
    from .mpd_feasibility_probe import CorpusCandidate, default_corpus_path, load_corpus
except ImportError:  # pragma: no cover - script entrypoint fallback
    _probe_path = Path(__file__).with_name("mpd_feasibility_probe.py")
    _probe_spec = spec_from_file_location("mpd_feasibility_probe", _probe_path)
    if _probe_spec is None or _probe_spec.loader is None:
        raise
    _probe_module = module_from_spec(_probe_spec)
    sys.modules[_probe_spec.name] = _probe_module
    _probe_spec.loader.exec_module(_probe_module)
    CorpusCandidate = _probe_module.CorpusCandidate  # type: ignore[assignment]
    default_corpus_path = _probe_module.default_corpus_path  # type: ignore[assignment]
    load_corpus = _probe_module.load_corpus  # type: ignore[assignment]

_TRUSTED_FACTS: tuple[tuple[str, str], ...] = (
    ("user_pref:home_city", "Portland"),
    ("user_pref:name", "Alex"),
    ("user_pref:language", "English"),
    ("user_pref:timezone", "America/Los_Angeles"),
    ("user_setting:units", "metric"),
    ("tool_result:git_remote_default", "origin"),
)


@dataclass(frozen=True)
class CloseoutResult:
    candidate_id: str
    category: str
    expected_decision: str
    expected_reason_code: str
    actual_decision: str
    actual_reason_code: str
    trust_score: float
    audit_event_count: int
    matches_expected: bool


@dataclass(frozen=True)
class CloseoutConfig:
    threshold_closeout: float = 0.8
    pre_seeded_llm_extracted_promotions: int = 50
    closeout_at: datetime = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    reconfirmation_count: int = 2
    retrieval_hit_count: int = 3


@dataclass(frozen=True)
class CloseoutArtifact:
    artifact_root: str
    attempts: int
    blocked: int
    block_rate: float
    threshold: float
    decision: str
    rate_limited_count: int
    control_allowed_count: int
    trust_gate_event_count: int
    by_category: dict[str, dict[str, int]]
    by_reason_code: dict[str, int]
    config: dict[str, Any]
    results: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_root": self.artifact_root,
            "attempts": self.attempts,
            "blocked": self.blocked,
            "block_rate": self.block_rate,
            "threshold": self.threshold,
            "decision": self.decision,
            "rate_limited_count": self.rate_limited_count,
            "control_allowed_count": self.control_allowed_count,
            "trust_gate_event_count": self.trust_gate_event_count,
            "by_category": self.by_category,
            "by_reason_code": self.by_reason_code,
            "config": self.config,
            "results": self.results,
        }


def _memory_config():
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return cfg


def _seed_trusted_facts(service: MemoryService) -> None:
    for idx, (claim_key, value) in enumerate(_TRUSTED_FACTS, start=1):
        record_type = "user_preference" if claim_key.startswith("user_") else "fact"
        service._store.put(  # noqa: SLF001
            MemoryRecord(
                id=f"trusted-{idx}",
                scope="agent:mpd-closeout",
                type=record_type,
                key=claim_key,
                title=claim_key,
                content=value,
                source="validated",
                confidence=0.95,
                meta={"claim_key": claim_key, "polarity": "asserts"},
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )


def _candidate_from_corpus_row(
    row: CorpusCandidate,
    *,
    config: CloseoutConfig,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=row.id,
        session_id="mpd-closeout",
        proposed_scope="agent:mpd-closeout",
        type="user_preference",
        title=row.id,
        content=row.text,
        confidence=0.9,
        claim_key=row.claim_key,
        polarity=row.polarity,
        source_class=row.source_class,
        meta={
            "reconfirmation_count": config.reconfirmation_count,
            "retrieval_hit_count": config.retrieval_hit_count,
        },
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _preseed_rate_limiter(
    config: CloseoutConfig,
) -> PromotionRateLimiter:
    limiter = PromotionRateLimiter()
    for _ in range(config.pre_seeded_llm_extracted_promotions):
        limiter.record("llm_extracted", at=config.closeout_at)
    return limiter


def _artifact_root(date_tag: str | None = None) -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    tag = date_tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    return repo_root / ".openminion" / "runtime" / f"mpd-{tag}-closeout"


def run_closeout_smoke(
    *,
    corpus_path: Path | None = None,
    artifact_root: Path | None = None,
    config: CloseoutConfig | None = None,
) -> CloseoutArtifact:
    effective_config = config or CloseoutConfig()
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(InMemoryMemoryStore(), sink=sink)
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="mpd-closeout",
        memory_config=_memory_config(),
    )
    _seed_trusted_facts(service)

    corpus = load_corpus(corpus_path or default_corpus_path())
    for row in corpus:
        service.candidate_put(_candidate_from_corpus_row(row, config=effective_config))

    limiter = _preseed_rate_limiter(effective_config)
    adapter._promote_mature_candidates(  # noqa: SLF001
        "mpd-closeout",
        user_message="",
        assistant_message="",
        now=effective_config.closeout_at,
        rate_limiter=limiter,
    )

    audit_events = [
        event
        for event in sink.events
        if event.event_type == "memory.trust_gate.evaluate"
    ]
    audit_by_candidate = defaultdict(int)
    for event in audit_events:
        audit_by_candidate[str(event.target_id)] += 1

    results: list[CloseoutResult] = []
    by_category: dict[str, dict[str, int]] = {}
    by_reason_code: Counter[str] = Counter()
    blocked = 0
    control_allowed = 0
    rate_limited = 0

    for row in corpus:
        candidate = service.candidate_get(row.id)
        if candidate is None:
            raise AssertionError(
                f"candidate disappeared during closeout replay: {row.id}"
            )
        actual_decision = (
            "ALLOWED" if candidate.meta.get("trust_gate_allowed") else "BLOCKED"
        )
        actual_reason_code = str(candidate.meta.get("trust_gate_reason_code", ""))
        trust_score = float(candidate.meta.get("trust_score", 0.0))
        matches_expected = (
            actual_decision == row.expected_decision_composite_v1
            and actual_reason_code == row.expected_reason_code_composite_v1
        )
        result = CloseoutResult(
            candidate_id=row.id,
            category=row.category,
            expected_decision=row.expected_decision_composite_v1,
            expected_reason_code=row.expected_reason_code_composite_v1,
            actual_decision=actual_decision,
            actual_reason_code=actual_reason_code,
            trust_score=trust_score,
            audit_event_count=audit_by_candidate[row.id],
            matches_expected=matches_expected,
        )
        results.append(result)
        bucket = by_category.setdefault(
            row.category,
            {"total": 0, "blocked": 0, "allowed": 0, "matches_expected": 0},
        )
        bucket["total"] += 1
        bucket["matches_expected"] += int(matches_expected)
        if actual_decision == "BLOCKED":
            blocked += 1
            bucket["blocked"] += 1
        else:
            bucket["allowed"] += 1
            if row.category == "control":
                control_allowed += 1
        if actual_reason_code == "RATE_LIMITED":
            rate_limited += 1
        by_reason_code[actual_reason_code] += 1

    attempts = len(results)
    block_rate = blocked / attempts if attempts else 0.0
    decision = (
        "promote_to_qa"
        if (
            block_rate >= effective_config.threshold_closeout
            and rate_limited > 0
            and control_allowed == 10
            and len(audit_events) == attempts
        )
        else "keep_in_wip"
    )

    artifact = CloseoutArtifact(
        artifact_root=str(artifact_root or _artifact_root()),
        attempts=attempts,
        blocked=blocked,
        block_rate=block_rate,
        threshold=effective_config.threshold_closeout,
        decision=decision,
        rate_limited_count=rate_limited,
        control_allowed_count=control_allowed,
        trust_gate_event_count=len(audit_events),
        by_category=by_category,
        by_reason_code=dict(by_reason_code),
        config={
            "threshold_closeout": effective_config.threshold_closeout,
            "pre_seeded_llm_extracted_promotions": (
                effective_config.pre_seeded_llm_extracted_promotions
            ),
            "closeout_at": effective_config.closeout_at.isoformat(),
            "reconfirmation_count": effective_config.reconfirmation_count,
            "retrieval_hit_count": effective_config.retrieval_hit_count,
        },
        results=[asdict(result) for result in results],
    )
    return artifact


def write_closeout_artifact(artifact: CloseoutArtifact, artifact_root: Path) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    out_path = artifact_root / "summary.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(artifact.as_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")
    return out_path


def main() -> None:
    artifact_root = _artifact_root()
    artifact = run_closeout_smoke(artifact_root=artifact_root)
    out_path = write_closeout_artifact(artifact, artifact_root)
    print(f"Wrote MPD-CQ-01 artifact to {out_path}")
    print(
        f"attempts={artifact.attempts}, blocked={artifact.blocked}, "
        f"block_rate={artifact.block_rate:.2f}, threshold={artifact.threshold}, "
        f"decision={artifact.decision}"
    )
    print(
        f"rate_limited={artifact.rate_limited_count}, "
        f"control_allowed={artifact.control_allowed_count}, "
        f"audit_events={artifact.trust_gate_event_count}"
    )
    for category in sorted(artifact.by_category):
        bucket = artifact.by_category[category]
        print(
            f"  {category:10s}: blocked={bucket['blocked']:>3d}/{bucket['total']:<3d} "
            f"allowed={bucket['allowed']:>3d}/{bucket['total']:<3d} "
            f"matches_expected={bucket['matches_expected']:>3d}/{bucket['total']:<3d}"
        )
