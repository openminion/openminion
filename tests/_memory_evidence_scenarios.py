"""Memory retention and purge on items with evidence, for SQLite and PostgreSQL.

Each case seeds ``artifact-ref`` (one evidence ref to an artifact),
``capture-evidence`` (the evidence entry ``apply_capture_bundle`` writes and
promotion copies to the record) and ``no-evidence``, then runs its step on the
store itself, not through ``AuditedMemoryStore``. The expected outcome is the
same on both backends.
"""

from __future__ import annotations

import datetime
from typing import Any

from openminion.modules.memory.models import (
    ArtifactRef,
    MemoryCandidate,
    MemoryRecord,
)
from openminion.modules.memory.runtime.gc import (
    apply_confidence_decay,
    compress_old_summaries,
    enforce_scope_capacity,
    evict_stale_insights,
)
from openminion.modules.memory.runtime.purge import purge_soft_deleted

SCOPE = "agent:evidence"
ARTIFACT_SHA256 = "a" * 64
EVIDENCE = {
    "artifact-ref": [
        ArtifactRef(
            ref=ARTIFACT_SHA256,
            mime="text/plain",
            sha256=ARTIFACT_SHA256,
            size_bytes=1,
        )
    ],
    "capture-evidence": [
        ArtifactRef(
            ref="capture-1",
            mime="application/x-openminion-capture",
            sha256="b" * 64,
            size_bytes=0,
            label="assured-capture",
        )
    ],
    "no-evidence": [],
}
ITEMS = tuple(EVIDENCE)
CASES = (
    "apply_confidence_decay",
    "compress_old_summaries",
    "enforce_scope_capacity",
    "evict_stale_insights",
    "purge_soft_deleted records",
    "purge_soft_deleted promoted candidates",
    "purge_soft_deleted rejected candidates",
)
# Only the artifact ref is an artifact edge; the capture entry is not.
REMOVED = [("memory", "artifact-ref", ARTIFACT_SHA256)]


class RecordingArtifactCtl:
    """Records the artifact ref edges that the memory store removes."""

    def __init__(self) -> None:
        self.removed: list[tuple[str, str, str]] = []

    def ref_add(self, owner_type: str, owner_id: str, target: str) -> None:
        return None

    def ref_remove(self, owner_type: str, owner_id: str, target: str) -> None:
        self.removed.append((owner_type, owner_id, target))

    def ref_remove_owner(self, owner_type: str, owner_id: str) -> None:
        self.removed.append((owner_type, owner_id, "(all)"))


def _iso_days_ago(days: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).isoformat()


def _put_record(
    store: Any,
    record_id: str,
    record_type: str,
    *,
    confidence: float,
    age_days: int,
    evidence: list[ArtifactRef],
) -> None:
    when = _iso_days_ago(age_days)
    store.put(
        MemoryRecord(
            id=record_id,
            scope=SCOPE,
            type=record_type,  # type: ignore[arg-type]
            title=record_id,
            content=f"{record_id}. Second sentence.",
            created_at=when,
            updated_at=when,
            confidence=confidence,
            evidence_refs=evidence,
        )
    )


def _seed_records(
    store: Any, record_type: str, *, confidence: float, age_days: int
) -> None:
    for item in ITEMS:
        _put_record(
            store,
            item,
            record_type,
            confidence=confidence,
            age_days=age_days,
            evidence=EVIDENCE[item],
        )


def _seed_candidates(store: Any, status: str) -> None:
    for item in ITEMS:
        store.candidate_put(
            MemoryCandidate(
                candidate_id=item,
                session_id="s1",
                proposed_scope=SCOPE,
                type="fact",
                content=item,
                evidence_refs=EVIDENCE[item],
                status=status,  # type: ignore[arg-type]
            )
        )


def _record_state(store: Any, record_id: str) -> Any:
    record = store.get(record_id)
    if record is None:
        return "purged"
    reason = record.deleted_reason if record.is_deleted else "active"
    return (round(float(record.confidence), 2), reason)


def _candidate_state(store: Any, candidate_id: str) -> str:
    candidate = store.candidate_get(candidate_id)
    return "purged" if candidate is None else str(candidate.status)


def _seed(case: str, store: Any) -> None:
    if case == "apply_confidence_decay":
        _seed_records(store, "fact", confidence=0.35, age_days=28)
    elif case == "compress_old_summaries":
        _seed_records(store, "session_summary", confidence=0.8, age_days=100)
    elif case == "enforce_scope_capacity":
        for index, item in enumerate(ITEMS):
            _put_record(
                store,
                item,
                "fact",
                confidence=0.1 * (index + 1),
                age_days=0,
                evidence=EVIDENCE[item],
            )
        _put_record(store, "keep", "fact", confidence=0.9, age_days=0, evidence=[])
    elif case == "evict_stale_insights":
        _seed_records(store, "meta_insight", confidence=0.8, age_days=100)
    elif case == "purge_soft_deleted records":
        _seed_records(store, "fact", confidence=0.8, age_days=0)
        for item in ITEMS:
            store.delete(item)
    else:
        _seed_candidates(store, case.split()[1])


def _step(case: str, store: Any) -> Any:
    # The arguments that the session lifecycle and `memctl gc` pass by default.
    if case == "apply_confidence_decay":
        return apply_confidence_decay(
            store, interval_days=7, decay_rate=0.05, min_confidence=0.3
        )
    if case == "compress_old_summaries":
        return compress_old_summaries(
            store, max_age_days=14, delete_age_days=90, max_summary_chars=100
        )
    if case == "enforce_scope_capacity":
        return enforce_scope_capacity(store, max_records=1)
    if case == "evict_stale_insights":
        return evict_stale_insights(store, staleness_days=60)
    result = purge_soft_deleted(store)
    return (result.deleted_records, result.deleted_candidates)


def run_case(case: str, store: Any, artifactctl: RecordingArtifactCtl) -> dict:
    """Seed the case's items, run its step on ``store`` and observe the outcome."""
    _seed(case, store)
    artifactctl.removed.clear()
    result = _step(case, store)
    candidates = case.endswith(" candidates")
    state = _candidate_state if candidates else _record_state
    items = list(ITEMS) + (["keep"] if case == "enforce_scope_capacity" else [])
    return {
        "result": result,
        "items": {item: state(store, item) for item in items},
        "artifact ref removals": sorted(artifactctl.removed),
    }


def expected_outcome(case: str) -> dict:
    """What every case gives on SQLite, and on PostgreSQL with #400 fixed."""
    soft_deleted = {
        "apply_confidence_decay": ((3, 3), 0.15, "confidence_below_threshold"),
        "compress_old_summaries": ((0, 3), 0.8, "summary_retention"),
        "evict_stale_insights": (3, 0.8, "stale_insight"),
    }
    if case in soft_deleted:
        result, confidence, reason = soft_deleted[case]
        items: dict[str, Any] = {item: (confidence, reason) for item in ITEMS}
    elif case == "enforce_scope_capacity":
        result = {SCOPE: 3}
        items = {
            "artifact-ref": (0.1, "scope_capacity"),
            "capture-evidence": (0.2, "scope_capacity"),
            "no-evidence": (0.3, "scope_capacity"),
            "keep": (0.9, "active"),
        }
    else:
        records = case == "purge_soft_deleted records"
        result = (3, 0) if records else (0, 3)
        items = {item: "purged" for item in ITEMS}
    return {"result": result, "items": items, "artifact ref removals": REMOVED}
