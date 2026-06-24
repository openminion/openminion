from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openminion.modules.memory.trust import PromotionRateLimiter, RateLimit

# Typed contracts


@dataclass(frozen=True)
class CorpusCandidate:
    id: str
    category: str
    text: str
    claim_key: str | None
    polarity: str
    source_class: str
    submitted_at: str
    expected_decision_minimum_viable: str
    expected_reason_code_minimum_viable: str
    expected_decision_composite_v1: str
    expected_reason_code_composite_v1: str

    @classmethod
    def from_jsonl_dict(cls, d: dict[str, Any]) -> "CorpusCandidate":
        return cls(
            id=d["id"],
            category=d["category"],
            text=d["text"],
            claim_key=d.get("claim_key"),
            polarity=d["polarity"],
            source_class=d["source_class"],
            submitted_at=d["submitted_at"],
            expected_decision_minimum_viable=d["expected_decision_minimum_viable"],
            expected_reason_code_minimum_viable=d[
                "expected_reason_code_minimum_viable"
            ],
            expected_decision_composite_v1=d["expected_decision_composite_v1"],
            expected_reason_code_composite_v1=d["expected_reason_code_composite_v1"],
        )


@dataclass(frozen=True)
class ProbeResult:
    candidate_id: str
    category: str
    expected_decision: str
    expected_reason_code: str
    actual_decision: str
    actual_reason_code: str
    matches_expected: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "expected_decision": self.expected_decision,
            "expected_reason_code": self.expected_reason_code,
            "actual_decision": self.actual_decision,
            "actual_reason_code": self.actual_reason_code,
            "matches_expected": self.matches_expected,
        }


DecideFn = Callable[[CorpusCandidate], tuple[str, str]]


# Reference probe config — documented in fixtures/README.md


@dataclass(frozen=True)
class ProbeConfig:
    min_trust_for_promotion: float = 0.5
    pre_seeded_llm_extracted_in_saturated_window: int = 50
    # Matches the corpus generator's ``_SATURATED_WINDOW_START`` constant
    pre_seed_timestamp: datetime = datetime(2026, 5, 20, 11, 30, 0, tzinfo=timezone.utc)
    rate_limit_llm_extracted_per_hour: int = 50
    rate_limit_agent_inferred_per_hour: int = 30
    rate_limit_tool_result_per_hour: int = 100
    rate_limit_user_input_per_hour: int = -1  # -1 sentinel: unlimited
    rate_limit_imported_bundle_per_hour: int = -1  # punt: not exercised by v1 corpus

    threshold_probe: float = 0.6
    threshold_closeout: float = 0.8


# Corpus loader + probe runner


def load_corpus(path: Path) -> list[CorpusCandidate]:
    out: list[CorpusCandidate] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(CorpusCandidate.from_jsonl_dict(json.loads(line)))
    return out


@dataclass
class ProbeArtifact:
    artifact_root: str
    attempts: int
    blocked: int
    block_rate: float
    threshold: float
    decision: str  # "unblock_full_lane" | "retain_design_only"
    ok: bool
    results: list[ProbeResult] = field(default_factory=list)
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_root": self.artifact_root,
            "attempts": self.attempts,
            "blocked": self.blocked,
            "block_rate": self.block_rate,
            "threshold": self.threshold,
            "decision": self.decision,
            "ok": self.ok,
            "by_category": self.by_category,
            "config": self.config,
            "results": [r.as_dict() for r in self.results],
        }


def run_probe(
    candidates: list[CorpusCandidate],
    *,
    decide: DecideFn,
    config: ProbeConfig,
    artifact_root: Path,
) -> ProbeArtifact:
    results: list[ProbeResult] = []
    by_category: dict[str, dict[str, int]] = {}

    for c in candidates:
        actual_decision, actual_reason_code = decide(c)
        matches = (
            actual_decision == c.expected_decision_minimum_viable
            and actual_reason_code == c.expected_reason_code_minimum_viable
        )
        results.append(
            ProbeResult(
                candidate_id=c.id,
                category=c.category,
                expected_decision=c.expected_decision_minimum_viable,
                expected_reason_code=c.expected_reason_code_minimum_viable,
                actual_decision=actual_decision,
                actual_reason_code=actual_reason_code,
                matches_expected=matches,
            )
        )
        cat = by_category.setdefault(
            c.category, {"total": 0, "blocked": 0, "allowed": 0, "matches_expected": 0}
        )
        cat["total"] += 1
        if actual_decision == "BLOCKED":
            cat["blocked"] += 1
        elif actual_decision == "ALLOWED":
            cat["allowed"] += 1
        if matches:
            cat["matches_expected"] += 1

    blocked = sum(1 for r in results if r.actual_decision == "BLOCKED")
    block_rate = blocked / len(candidates) if candidates else 0.0
    decision = (
        "unblock_full_lane"
        if block_rate >= config.threshold_probe
        else "retain_design_only"
    )

    return ProbeArtifact(
        artifact_root=str(artifact_root),
        attempts=len(candidates),
        blocked=blocked,
        block_rate=block_rate,
        threshold=config.threshold_probe,
        decision=decision,
        ok=True,
        results=results,
        by_category=by_category,
        config={
            "min_trust_for_promotion": config.min_trust_for_promotion,
            "pre_seeded_llm_extracted_in_saturated_window": (
                config.pre_seeded_llm_extracted_in_saturated_window
            ),
            "rate_limit_llm_extracted_per_hour": (
                config.rate_limit_llm_extracted_per_hour
            ),
            "rate_limit_agent_inferred_per_hour": (
                config.rate_limit_agent_inferred_per_hour
            ),
            "rate_limit_tool_result_per_hour": config.rate_limit_tool_result_per_hour,
            "rate_limit_user_input_per_hour": config.rate_limit_user_input_per_hour,
            "threshold_probe": config.threshold_probe,
            "threshold_closeout": config.threshold_closeout,
        },
    )


def write_artifact(artifact: ProbeArtifact, artifact_root: Path) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    out_path = artifact_root / "summary.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(artifact.as_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")
    return out_path


class MinimumViableGate:
    # Spec D2 closed-set provenance table.
    _PROVENANCE_TABLE = {
        "user_input": 1.0,
        "tool_result": 0.8,
        "imported_bundle": 0.6,
        "llm_extracted": 0.5,
        "agent_inferred": 0.4,
    }

    def __init__(self, config: ProbeConfig) -> None:
        self.config = config
        self._rate_limiter = PromotionRateLimiter(
            {
                "user_input": RateLimit(
                    source_class="user_input",
                    window_seconds=3600,
                    max_promotions=(
                        None
                        if config.rate_limit_user_input_per_hour < 0
                        else config.rate_limit_user_input_per_hour
                    ),
                ),
                "tool_result": RateLimit(
                    source_class="tool_result",
                    window_seconds=3600,
                    max_promotions=config.rate_limit_tool_result_per_hour,
                ),
                "llm_extracted": RateLimit(
                    source_class="llm_extracted",
                    window_seconds=3600,
                    max_promotions=config.rate_limit_llm_extracted_per_hour,
                ),
                "agent_inferred": RateLimit(
                    source_class="agent_inferred",
                    window_seconds=3600,
                    max_promotions=config.rate_limit_agent_inferred_per_hour,
                ),
                "imported_bundle": RateLimit(
                    source_class="imported_bundle",
                    window_seconds=0,
                    max_promotions=(
                        None
                        if config.rate_limit_imported_bundle_per_hour < 0
                        else config.rate_limit_imported_bundle_per_hour
                    ),
                ),
            }
        )
        for _ in range(config.pre_seeded_llm_extracted_in_saturated_window):
            self._rate_limiter.record(
                "llm_extracted",
                at=config.pre_seed_timestamp,
            )

    def decide(self, candidate: CorpusCandidate) -> tuple[str, str]:
        # 1. claim_key presence / structural validity (fail-closed).
        if not candidate.claim_key or not str(candidate.claim_key).strip():
            return ("BLOCKED", "MISSING_CLAIM_KEY")

        # Parse candidate timestamp.
        ts = datetime.fromisoformat(candidate.submitted_at)

        # 2. Rate-limit accounting.
        decision = self._rate_limiter.assess(candidate.source_class, at=ts)
        if not decision.allowed:
            return ("BLOCKED", "RATE_LIMITED")

        # 3. Composite minimum-viable score.
        provenance = self._PROVENANCE_TABLE.get(candidate.source_class, 0.0)
        pressure = (
            decision.observed_promotions / decision.max_promotions
            if decision.max_promotions
            else 0.0
        )
        score = provenance * (1.0 - pressure)

        if score < self.config.min_trust_for_promotion:
            return ("BLOCKED", "BELOW_TRUST_THRESHOLD")

        # 4. Allowed — record this candidate's timestamp for future pressure.
        self._rate_limiter.record(candidate.source_class, at=ts)
        return ("ALLOWED", "ALLOWED")


# Temporary rule-based decision stub for the feasibility probe.


def decide_using_expected_fields(candidate: CorpusCandidate) -> tuple[str, str]:
    return (
        candidate.expected_decision_minimum_viable,
        candidate.expected_reason_code_minimum_viable,
    )


def default_artifact_root() -> Path:
    here = Path(__file__).resolve()
    # Climb from probes/ to the workspace root.
    repo_root = here.parents[4]
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    return repo_root / ".openminion" / "runtime" / f"mpd-{date_tag}-feasibility"


def default_corpus_path() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1] / "fixtures" / "memory_injection_corpus_v1.jsonl"


def main() -> None:
    config = ProbeConfig()
    corpus = load_corpus(default_corpus_path())
    artifact_root = default_artifact_root()
    gate = MinimumViableGate(config)
    artifact = run_probe(
        corpus,
        decide=gate.decide,
        config=config,
        artifact_root=artifact_root,
    )
    out = write_artifact(artifact, artifact_root)
    print(f"Wrote MPD-00a artifact to {out}")
    print(
        f"attempts={artifact.attempts}, blocked={artifact.blocked}, "
        f"block_rate={artifact.block_rate:.2f}, threshold={artifact.threshold}, "
        f"decision={artifact.decision}"
    )
    # Surface per-category breakdown for quick triage
    for cat in sorted(artifact.by_category):
        b = artifact.by_category[cat]
        print(
            f"  {cat:10s}: blocked={b['blocked']:>3d}/{b['total']:<3d} "
            f"matches_expected={b['matches_expected']:>3d}/{b['total']:<3d}"
        )
