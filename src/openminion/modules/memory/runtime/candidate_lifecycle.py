"""Candidate and reinforcement helpers for ``MemoryService``."""

from typing import Any
import uuid

from openminion.modules.memory.constants import MEMORY_CANDIDATE_STATUS_PROPOSED
from openminion.modules.memory.errors import (
    InvalidArgumentError,
    NotFoundError,
    PromotionDeniedError,
)
from openminion.modules.memory.models import (
    ArtifactRef,
    MemoryCandidate,
    MemoryRecord,
    MemoryScope,
    _as_memory_type,
    _as_memory_type_list,
)
from openminion.modules.memory.runtime.scope import (
    assert_scope_matches_agent,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)


def _resolve_learning_bounds(config: Any | None) -> tuple[float, float]:
    confidence_boost = 0.1
    confidence_max = 0.9
    if config is None:
        return confidence_boost, confidence_max
    try:
        confidence_boost = float(
            getattr(config, "confidence_boost_per_reconfirmation", 0.1)
        )
    except (TypeError, ValueError):
        confidence_boost = 0.1
    try:
        confidence_max = float(getattr(config, "confidence_max", 0.9))
    except (TypeError, ValueError):
        confidence_max = 0.9
    return confidence_boost, confidence_max


def _is_live_record(record: MemoryRecord) -> bool:
    if bool(getattr(record, "is_deleted", False)):
        return False
    if bool(getattr(record, "valid_to", None)) and record.is_invalidated_at():
        return False
    return not bool(getattr(record, "superseded_by_id", None))


class MemoryCandidateLifecycle:
    """Internal owner for candidate and reinforcement flows."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def candidate_put(self, candidate: MemoryCandidate) -> str:
        return self._service._store.candidate_put(candidate)  # noqa: SLF001

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> str:
        if agent_id:
            assert_scope_matches_agent(scope, agent_id)
        normalized_scope = str(MemoryScope.coerce(scope))
        refs = [
            ArtifactRef(
                ref=str(ref),
                mime="application/octet-stream",
                sha256="unknown",
                size_bytes=0,
                label=f"evidence-{ref}",
            )
            for ref in evidence_refs or []
        ]
        resolved_meta = dict(meta or {})
        normalized_key_from_meta = str(
            resolved_meta.get("normalized_key") or ""
        ).strip()
        candidate = MemoryCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:12]}",
            session_id=MemoryScope.parse(normalized_scope).value,
            proposed_scope=normalized_scope,
            type=_as_memory_type(record_type),
            title=title,
            content=content,
            tags=list(tags or []),
            confidence=float(confidence) if confidence is not None else 0.5,
            evidence_refs=refs,
            key=normalized_key_from_meta or None,
            meta=resolved_meta,
        )
        return self._service._store.candidate_put(candidate)  # noqa: SLF001

    def candidate_get(self, candidate_id: str) -> MemoryCandidate:
        candidate = self._service._store.candidate_get(candidate_id)  # noqa: SLF001
        if not candidate:
            raise NotFoundError(
                f"Candidate {candidate_id!r} not found", details={"id": candidate_id}
            )
        return candidate

    def candidate_list(self, options: CandidateListOptions) -> list[MemoryCandidate]:
        return self._service._store.candidate_list(options)  # noqa: SLF001

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate:
        try:
            return self._service._store.candidate_update(  # noqa: SLF001
                candidate_id, patch
            )
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc

    def find_candidate_by_normalized_key(
        self, *, scope: str, normalized_key: str
    ) -> str | None:
        key = str(normalized_key or "").strip()
        scope_value = str(scope or "").strip()
        if not key or not scope_value:
            return None

        options = CandidateListOptions(
            proposed_scope=scope_value,
            status=MEMORY_CANDIDATE_STATUS_PROPOSED,
            limit=None,
        )
        try:
            candidates = self._service._store.candidate_list(options)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            return None

        matched_id: str | None = None
        matched_updated_at: Any = None
        for candidate in candidates or []:
            meta = getattr(candidate, "meta", None) or {}
            if str(meta.get("normalized_key") or "").strip() != key:
                continue
            candidate_id = str(getattr(candidate, "candidate_id", "") or "").strip()
            if not candidate_id:
                continue
            updated_at = getattr(candidate, "updated_at", None) or getattr(
                candidate, "created_at", None
            )
            if matched_id is None or (
                updated_at is not None
                and matched_updated_at is not None
                and updated_at > matched_updated_at
            ):
                matched_id = candidate_id
                matched_updated_at = updated_at
        return matched_id

    def reinforce_candidate(self, *, candidate_id: str) -> MemoryCandidate:
        candidate = self.candidate_get(candidate_id)
        original_meta = dict(getattr(candidate, "meta", {}) or {})
        confidence_boost, confidence_max = _resolve_learning_bounds(
            getattr(self._service, "_candidate_learning_config", None)  # noqa: SLF001
        )
        updated_meta = dict(original_meta)
        updated_meta["reconfirmation_count"] = (
            int(updated_meta.get("reconfirmation_count", 0) or 0) + 1
        )
        current_confidence = float(getattr(candidate, "confidence", 0.0) or 0.0)
        new_confidence = min(confidence_max, current_confidence + confidence_boost)
        return self.candidate_update(
            candidate_id,
            {
                "meta": updated_meta,
                "confidence": new_confidence,
            },
        )

    def find_record_by_normalized_key(
        self,
        *,
        scope: str,
        record_type: str,
        normalized_key: str,
    ) -> MemoryRecord | None:
        key = str(normalized_key or "").strip()
        scope_value = str(scope or "").strip()
        record_type_value = str(record_type or "").strip()
        if not key or not scope_value or not record_type_value:
            return None
        try:
            hits = self._service._store.list(  # noqa: SLF001
                ListQueryOptions(
                    scopes=[scope_value],
                    types=_as_memory_type_list([record_type_value]),
                    limit=None,
                )
            )
        except Exception:  # noqa: BLE001
            return None
        for record in hits or []:
            if str(getattr(record, "key", "") or "").strip() != key:
                continue
            if _is_live_record(record):
                return record
        return None

    def reinforce_record(self, *, record_id: str) -> MemoryRecord:
        record = self._service.get(record_id)
        confidence_boost, confidence_max = _resolve_learning_bounds(
            getattr(self._service, "_candidate_learning_config", None)  # noqa: SLF001
        )
        current_confidence = float(getattr(record, "confidence", 0.0) or 0.0)
        new_confidence = min(confidence_max, current_confidence + confidence_boost)
        key = str(getattr(record, "key", "") or "").strip()
        if not key:
            try:
                self._service._store.touch_last_hit(record_id)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
            return record
        return self._service._store.upsert(  # noqa: SLF001
            record.scope,
            record.type,
            key,
            {
                "content": record.content,
                "tags": list(record.tags or []),
                "entities": list(record.entities or []),
                "source": record.source,
                "confidence": new_confidence,
                "evidence_refs": list(record.evidence_refs or []),
                "meta": dict(record.meta or {}),
                "title": record.title,
            },
        )

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        candidate = self.candidate_get(candidate_id)
        decision = self._service._promotion_policy.evaluate(  # noqa: SLF001
            candidate, target_scope
        )
        self._service._record_policy_decision(  # noqa: SLF001
            lane="promotion", decision=decision
        )
        if not bool(getattr(decision, "allowed", False)):
            raise PromotionDeniedError(
                str(getattr(decision, "reason", "") or "Promotion denied by policy"),
                details={
                    "candidate_id": candidate_id,
                    "target_scope": target_scope,
                    "reason_code": str(
                        getattr(decision, "reason_code", "promotion_denied")
                    ),
                },
            )
        try:
            return self._service._store.promote_candidate(  # noqa: SLF001
                candidate_id, target_scope
            )
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc
