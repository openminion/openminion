"""Typed proof-packet ingestion for guarded continuous learning."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.runtime.improvement.candidates import (
    ImprovementCandidate,
    ImprovementCandidateSemanticAuthorSource,
    ImprovementCandidateStageResult,
    ImprovementCandidateTarget,
    stage_learning_memory_candidate,
)

ProofLearningStatus = Literal[
    "disabled",
    "recorded",
    "duplicate_observation",
    "threshold_not_met",
    "staged",
    "skipped",
]
ProofLearningReason = Literal[
    "",
    "ingestion_disabled",
    "duplicate_observation",
    "semantic_author_source_required",
    "learning_target_required",
    "candidate_state_not_stageable",
    "unsupported_target_owner",
]


class ProofPacketLearningSubmission(BaseModel):
    """Caller-authored semantic learning proposal backed by proof evidence."""

    model_config = ConfigDict(extra="forbid")

    submission_id: str
    proof_packet_ref: str
    run_id: str
    target_type: ImprovementCandidateTarget | None = None
    target_owner: str = ""
    semantic_summary: str = ""
    semantic_author_source: ImprovementCandidateSemanticAuthorSource | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    candidate_state: Literal[
        "staged",
        "under_review",
        "suppressed",
        "promoted",
        "rolled_back",
        "rejected",
    ] = "staged"
    validation_refs: list[str] = Field(default_factory=list)

    @property
    def stageable(self) -> bool:
        return (
            self.target_type is not None
            and bool(self.target_owner.strip())
            and bool(self.semantic_summary.strip())
            and self.semantic_author_source is not None
            and self.candidate_state == "staged"
        )


class ProofPacketEvidenceBundle(BaseModel):
    """Aggregated structural evidence for one declared semantic proposal."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    target_type: ImprovementCandidateTarget
    target_owner: str
    semantic_summary: str
    semantic_author_source: ImprovementCandidateSemanticAuthorSource
    submission_ids: list[str]
    proof_packet_refs: list[str]
    run_ids: list[str]
    evidence_refs: list[str]
    validation_refs: list[str]

    @property
    def observation_count(self) -> int:
        return len(self.submission_ids)


class ProofPacketIngestionResult(BaseModel):
    """Result of ingesting one proof-packet learning submission."""

    model_config = ConfigDict(extra="forbid")

    status: ProofLearningStatus
    reason_code: ProofLearningReason = ""
    submission_id: str = ""
    bundle: ProofPacketEvidenceBundle | None = None
    stage_result: ImprovementCandidateStageResult | None = None


@dataclass
class ProofPacketLearningStore:
    """File-backed structural observation store for proof-packet learning."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self, submission: ProofPacketLearningSubmission
    ) -> tuple[bool, ProofPacketEvidenceBundle | None]:
        payload = self._load()
        observations = payload.setdefault("observations", {})
        if submission.submission_id in observations:
            return False, self.bundle_for(submission)
        observations[submission.submission_id] = submission.model_dump(mode="json")
        self._write(payload)
        return True, self.bundle_for(submission)

    def bundle_for(
        self, submission: ProofPacketLearningSubmission
    ) -> ProofPacketEvidenceBundle | None:
        if not submission.stageable:
            return None
        matching = [
            item
            for item in self._load().get("observations", {}).values()
            if _bundle_key(ProofPacketLearningSubmission.model_validate(item))
            == _bundle_key(submission)
        ]
        if not matching:
            return None
        submissions = [
            ProofPacketLearningSubmission.model_validate(item)
            for item in sorted(matching, key=lambda item: item["submission_id"])
        ]
        return _bundle_from_submissions(submissions)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"observations": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def ingest_proof_packet_learning_submission(
    submission: ProofPacketLearningSubmission | dict[str, Any],
    *,
    store: ProofPacketLearningStore,
    memory_service: Any | None = None,
    session_id: str = "",
    agent_id: str = "",
    min_observations: int = 2,
    enabled: bool = True,
) -> ProofPacketIngestionResult:
    """Record one typed proof submission and stage a memory candidate if ready."""

    submission_obj = (
        submission
        if isinstance(submission, ProofPacketLearningSubmission)
        else ProofPacketLearningSubmission.model_validate(submission)
    )
    if not enabled:
        return ProofPacketIngestionResult(
            status="disabled",
            reason_code="ingestion_disabled",
            submission_id=submission_obj.submission_id,
        )
    if submission_obj.target_type is None:
        return ProofPacketIngestionResult(
            status="skipped",
            reason_code="learning_target_required",
            submission_id=submission_obj.submission_id,
        )
    if submission_obj.semantic_author_source is None:
        return ProofPacketIngestionResult(
            status="skipped",
            reason_code="semantic_author_source_required",
            submission_id=submission_obj.submission_id,
        )
    if submission_obj.candidate_state != "staged":
        return ProofPacketIngestionResult(
            status="skipped",
            reason_code="candidate_state_not_stageable",
            submission_id=submission_obj.submission_id,
        )
    if submission_obj.target_type != "memory":
        return ProofPacketIngestionResult(
            status="skipped",
            reason_code="unsupported_target_owner",
            submission_id=submission_obj.submission_id,
        )

    recorded, bundle = store.record(submission_obj)
    if not recorded:
        return ProofPacketIngestionResult(
            status="duplicate_observation",
            reason_code="duplicate_observation",
            submission_id=submission_obj.submission_id,
            bundle=bundle,
        )
    if bundle is None or bundle.observation_count < min_observations:
        return ProofPacketIngestionResult(
            status="threshold_not_met",
            submission_id=submission_obj.submission_id,
            bundle=bundle,
        )
    if memory_service is None:
        return ProofPacketIngestionResult(
            status="recorded",
            submission_id=submission_obj.submission_id,
            bundle=bundle,
        )

    stage_result = stage_learning_memory_candidate(
        ImprovementCandidate(
            candidate_id=f"proof-learning-{bundle.bundle_id}",
            target_type="memory",
            target_owner=bundle.target_owner,
            summary=bundle.semantic_summary,
            evidence_refs=bundle.evidence_refs,
            semantic_author_source=bundle.semantic_author_source,
        ),
        memory_service=memory_service,
        session_id=session_id,
        agent_id=agent_id,
        trace_id=submission_obj.proof_packet_ref,
    )
    return ProofPacketIngestionResult(
        status="staged" if stage_result.status == "staged" else "skipped",
        submission_id=submission_obj.submission_id,
        bundle=bundle,
        stage_result=stage_result,
    )


def _bundle_key(
    submission: ProofPacketLearningSubmission,
) -> tuple[str, str, str, str]:
    return (
        str(submission.target_type or ""),
        submission.target_owner.strip(),
        submission.semantic_summary.strip(),
        str(submission.semantic_author_source or ""),
    )


def _bundle_from_submissions(
    submissions: list[ProofPacketLearningSubmission],
) -> ProofPacketEvidenceBundle:
    first = submissions[0]
    evidence_refs = _unique(
        ref
        for item in submissions
        for ref in [item.proof_packet_ref, *item.evidence_refs]
        if ref
    )
    validation_refs = _unique(
        ref for item in submissions for ref in item.validation_refs if ref
    )
    proof_refs = _unique(item.proof_packet_ref for item in submissions)
    run_ids = _unique(item.run_id for item in submissions)
    submission_ids = _unique(item.submission_id for item in submissions)
    digest = sha256(
        json.dumps(
            {
                "key": _bundle_key(first),
                "proof_packet_refs": proof_refs,
                "submission_ids": submission_ids,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return ProofPacketEvidenceBundle(
        bundle_id=digest,
        target_type=first.target_type or "memory",
        target_owner=first.target_owner,
        semantic_summary=first.semantic_summary,
        semantic_author_source=first.semantic_author_source or "imported",
        submission_ids=submission_ids,
        proof_packet_refs=proof_refs,
        run_ids=run_ids,
        evidence_refs=evidence_refs,
        validation_refs=validation_refs,
    )


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


__all__ = [
    "ProofPacketEvidenceBundle",
    "ProofPacketIngestionResult",
    "ProofPacketLearningStore",
    "ProofPacketLearningSubmission",
    "ingest_proof_packet_learning_submission",
]
