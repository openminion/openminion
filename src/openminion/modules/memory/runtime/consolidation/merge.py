"""Phase 2 merge/review helpers for memory consolidation."""

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from threading import Lock, RLock
from typing import Any

from openminion.modules.llm import RuntimeLLMHandle
from openminion.modules.llm.schemas import Message
from openminion.modules.memory.constants import MEMORY_CANDIDATE_STATUS_PROPOSED
from openminion.modules.memory.errors import (
    InvalidArgumentError,
    NotFoundError,
    PromotionDeniedError,
)
from openminion.modules.memory.models import CandidateReview
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ConsolidationConfig,
    ExtractionPayload,
    MergeDecision,
    MergeDecisions,
)
from openminion.modules.memory.runtime.consolidation.backend_access import (
    memory_backend,
)

_CONSOLIDATION_LOCK_GUARD = Lock()
_CONSOLIDATION_LOCKS: dict[str, RLock] = {}
_MERGE_ACTIONS = frozenset({"promote", "keep", "defer", "discard"})
_MERGE_SYSTEM_PROMPT = (
    "You are reviewing a memory consolidation extraction payload. "
    "Return structured memory_consolidation decisions only for the provided "
    "candidate_ids. Allowed actions are promote, keep, defer, or discard. "
    "Use keep when the candidate is already represented and should not change "
    "durable state. Keep reasoning concise."
)


def resolve_consolidation_model_handle(
    primary_handle: RuntimeLLMHandle,
    config: ConsolidationConfig,
) -> RuntimeLLMHandle:
    configured_model = str(config.consolidation_model or "").strip()
    if not configured_model or configured_model == primary_handle.model:
        return primary_handle
    return RuntimeLLMHandle(
        name=primary_handle.name,
        model=configured_model,
        client=primary_handle.client,
        tool_call_strategy=primary_handle.tool_call_strategy,
        retry_override_policy=primary_handle.retry_override_policy,
        service_vendor=primary_handle.service_vendor,
        provider_retry_max_attempts=primary_handle.provider_retry_max_attempts,
    )


def consolidation_lock_key(session_id: str, agent_id: str) -> str:
    return f"{str(session_id or '').strip()}::{str(agent_id or '').strip()}"


def _get_lock(key: str) -> RLock:
    with _CONSOLIDATION_LOCK_GUARD:
        lock = _CONSOLIDATION_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _CONSOLIDATION_LOCKS[key] = lock
        return lock


@contextmanager
def acquire_consolidation_lock(session_id: str, agent_id: str):
    lock = _get_lock(consolidation_lock_key(session_id, agent_id))
    with lock:
        yield


def _normalize_merge_decisions(payload: Any) -> list[MergeDecision]:
    raw_items = (
        list(payload.get("decisions") or []) if isinstance(payload, dict) else []
    )
    decisions: list[MergeDecision] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id", "") or "").strip()
        action = str(item.get("action", "") or "").strip().lower()
        reasoning = str(item.get("reasoning", "") or "").strip()
        if not candidate_id or action not in _MERGE_ACTIONS:
            continue
        decisions.append(
            MergeDecision(
                candidate_id=candidate_id,
                action=action,
                reasoning=reasoning,
            )
        )
    return decisions


def _fallback_defer_all(payload: ExtractionPayload, *, reason: str) -> MergeDecisions:
    return MergeDecisions(
        decisions=[
            MergeDecision(
                candidate_id=str(item.get("candidate_id", "") or "").strip(),
                action="defer",
                reasoning=reason,
            )
            for item in payload.candidate_refs
            if str(item.get("candidate_id", "") or "").strip()
        ],
        review_notes=[reason],
    )


def run_consolidation_merge(
    payload: ExtractionPayload,
    consolidation_model_handle: RuntimeLLMHandle,
) -> MergeDecisions:
    complete = getattr(consolidation_model_handle.client, "complete", None)
    if not callable(complete):
        fallback = _fallback_defer_all(
            payload,
            reason="consolidation model client does not support complete()",
        )
        return MergeDecisions(
            decisions=fallback.decisions,
            model_name=consolidation_model_handle.model,
            review_notes=fallback.review_notes,
        )
    request_payload = {
        "session_id": payload.session_id,
        "agent_id": payload.agent_id,
        "candidate_refs": payload.candidate_refs,
        "topic_clusters": payload.topic_clusters,
        "contradiction_hints": payload.contradiction_hints,
        "duplicate_hints": payload.duplicate_hints,
        "evidence_window": payload.evidence_window,
    }
    with acquire_consolidation_lock(payload.session_id, payload.agent_id):
        response = complete(
            messages=[
                Message(role="system", content=_MERGE_SYSTEM_PROMPT),
                Message(
                    role="user",
                    content=json.dumps(request_payload, sort_keys=True),
                ),
            ],
            tools=None,
            model=consolidation_model_handle.model,
            tool_choice="none",
            metadata={"purpose": "memory_consolidation_merge"},
        )
    decisions = _normalize_merge_decisions(
        getattr(response, "memory_consolidation", None)
    )
    if not decisions:
        fallback = _fallback_defer_all(
            payload,
            reason="consolidation merge returned no structured decisions",
        )
        return MergeDecisions(
            decisions=fallback.decisions,
            model_name=consolidation_model_handle.model,
            review_notes=fallback.review_notes,
        )
    return MergeDecisions(
        decisions=decisions,
        model_name=consolidation_model_handle.model,
        review_notes=[str(getattr(response, "output_text", "") or "").strip()],
    )


def _hint_record_id(
    payload: ExtractionPayload,
    *,
    candidate_id: str,
) -> str | None:
    for item in payload.duplicate_hints:
        if str(item.get("candidate_id", "") or "").strip() == candidate_id:
            record_id = str(item.get("existing_record_id", "") or "").strip()
            if record_id:
                return record_id
    for item in payload.contradiction_hints:
        if str(item.get("candidate_id", "") or "").strip() == candidate_id:
            record_id = str(item.get("record_id", "") or "").strip()
            if record_id:
                return record_id
    return None


def _consolidation_meta(
    existing_meta: dict[str, Any] | None,
    *,
    action: str,
    reasoning: str,
    decided_at: str,
) -> dict[str, Any]:
    merged = dict(existing_meta or {})
    merged.update(
        {
            "consolidation_action": action,
            "consolidation_reasoning": reasoning,
            "consolidation_decided_at": decided_at,
        }
    )
    return merged


def _apply_merge_action(
    candidate: Any,
    apply_decision: Any,
    *,
    candidate_id: str,
    action: str,
    reasoning: str,
    target_scope: str,
    reviewer: str,
    decided_at: str,
) -> Any:
    if candidate is None:
        raise NotFoundError(f"candidate not found: {candidate_id}")
    if action == "keep":
        if (
            str(candidate.status) != MEMORY_CANDIDATE_STATUS_PROPOSED
            or candidate.proposed_scope != target_scope
        ):
            raise InvalidArgumentError(
                f"candidate {candidate_id} is outside the active consolidation scope"
            )
        return None
    return apply_decision(
        candidate,
        action=action,
        target_scope=target_scope,
        review=CandidateReview(
            reviewer=reviewer,
            decided_at=decided_at,
            note=reasoning or None,
        ),
        meta=_consolidation_meta(
            getattr(candidate, "meta", {}) or {},
            action=action,
            reasoning=reasoning,
            decided_at=decided_at,
        ),
    )


def _empty_merge_result(error: str) -> dict[str, Any]:
    return {
        "applied_count": 0,
        "promoted_count": 0,
        "discarded_count": 0,
        "deferred_count": 0,
        "kept_count": 0,
        "errors": [error],
        "supersession_errors": [],
    }


def _apply_consolidation_hint(
    handler: Any,
    payload: ExtractionPayload,
    *,
    candidate_id: str,
    promoted_record_id: str,
    target_scope: str,
    reason: str,
) -> tuple[str | None, str | None]:
    prior_record_id = _hint_record_id(payload, candidate_id=candidate_id)
    if not prior_record_id:
        return None, None
    if not callable(handler):
        return None, "checked consolidation supersession is unsupported"
    try:
        superseded = handler(
            prior_record_id,
            promoted_record_id,
            target_scope=target_scope,
            reason=reason or "memory_consolidation",
        )
    except (InvalidArgumentError, NotFoundError) as exc:
        return None, str(exc)
    return str(getattr(superseded, "id", "") or "").strip(), None


def apply_merge_decisions_via_service(
    memory_service: Any,
    *,
    payload: ExtractionPayload,
    merge_decisions: MergeDecisions,
    target_scope: str,
    reviewer: str = "memory_consolidation",
) -> dict[str, Any]:
    candidate_get = getattr(memory_service, "candidate_get", None)
    apply_decision = getattr(memory_service, "apply_consolidation_decision", None)
    supersede_consolidation_hint = getattr(
        memory_service, "supersede_consolidation_hint", None
    )
    expected_scope = f"agent:{str(payload.agent_id or '').strip()}"
    normalized_target_scope = str(target_scope or "").strip()
    if expected_scope == "agent:" or normalized_target_scope != expected_scope:
        return _empty_merge_result(
            "target scope does not match the consolidation agent"
        )
    if not callable(candidate_get) or not callable(apply_decision):
        return _empty_merge_result(
            "memory service does not support consolidation decisions"
        )

    decided_at = datetime.now(timezone.utc).isoformat()
    counters: Counter[str] = Counter()
    errors: list[str] = []
    applied_ids: list[str] = []
    promoted_record_ids: list[str] = []
    superseded_record_ids: list[str] = []
    supersession_errors: list[str] = []
    payload_candidate_ids = {
        str(item.get("candidate_id", "") or "").strip()
        for item in payload.candidate_refs
    }
    decision_counts = Counter(
        str(decision.candidate_id or "").strip()
        for decision in merge_decisions.decisions
    )

    for decision in merge_decisions.decisions:
        candidate_id = str(decision.candidate_id or "").strip()
        action = str(decision.action or "").strip().lower()
        reasoning = str(decision.reasoning or "").strip()
        if not candidate_id or action not in _MERGE_ACTIONS:
            continue
        if candidate_id not in payload_candidate_ids:
            errors.append(f"{candidate_id}: candidate is not in the extraction payload")
            continue
        if decision_counts[candidate_id] != 1:
            errors.append(f"{candidate_id}: duplicate consolidation decision")
            continue
        try:
            applied = _apply_merge_action(
                candidate_get(candidate_id),
                apply_decision,
                candidate_id=candidate_id,
                action=action,
                reasoning=reasoning,
                target_scope=normalized_target_scope,
                reviewer=reviewer,
                decided_at=decided_at,
            )
        except PromotionDeniedError as exc:
            errors.append(f"{candidate_id}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{candidate_id}: {exc}")
            continue
        counters[action] += 1
        applied_ids.append(candidate_id)
        if action != "promote":
            continue
        promoted_record_id = str(getattr(applied, "id", "") or "").strip()
        promoted_record_ids.append(promoted_record_id)
        superseded_id, hint_error = _apply_consolidation_hint(
            supersede_consolidation_hint,
            payload,
            candidate_id=candidate_id,
            promoted_record_id=promoted_record_id,
            target_scope=normalized_target_scope,
            reason=reasoning,
        )
        if superseded_id:
            superseded_record_ids.append(superseded_id)
        if hint_error:
            supersession_errors.append(f"{candidate_id}: {hint_error}")

    return {
        "applied_count": sum(counters.values()),
        "promoted_count": counters["promote"],
        "discarded_count": counters["discard"],
        "deferred_count": counters["defer"],
        "kept_count": counters["keep"],
        "applied_candidate_ids": applied_ids,
        "promoted_record_ids": promoted_record_ids,
        "superseded_record_ids": superseded_record_ids,
        "errors": errors,
        "supersession_errors": supersession_errors,
    }


def apply_memory_consolidation_decisions(
    memory_api: Any,
    *,
    decisions: list[dict[str, Any]],
    target_scope: str,
    selected_candidate_ids: list[str],
    reviewer: str = "memory_consolidation",
) -> dict[str, Any]:
    backend = memory_backend(memory_api)
    candidate_get = getattr(backend, "candidate_get", None)
    apply_decision = getattr(backend, "apply_consolidation_decision", None)
    if not callable(candidate_get) or not callable(apply_decision):
        return {
            "applied_count": 0,
            "promoted_count": 0,
            "discarded_count": 0,
            "deferred_count": 0,
            "errors": ["memory backend does not support consolidation decisions"],
        }

    decided_at = datetime.now(timezone.utc).isoformat()
    counters: Counter[str] = Counter()
    errors: list[str] = []
    applied_ids: list[str] = []
    selected_counts = Counter(
        candidate_id
        for item in selected_candidate_ids
        if (candidate_id := str(item or "").strip())
    )
    decision_counts = Counter(
        candidate_id
        for item in decisions
        if (candidate_id := str(item.get("candidate_id", "") or "").strip())
    )
    for item in decisions:
        candidate_id = str(item.get("candidate_id", "") or "").strip()
        action = str(item.get("action", "") or "").strip().lower()
        reasoning = str(item.get("reasoning", "") or "").strip()
        if not candidate_id or action not in {"promote", "discard", "defer"}:
            continue
        if selected_counts[candidate_id] != 1:
            errors.append(f"{candidate_id}: candidate is not in the selected batch")
            continue
        if decision_counts[candidate_id] != 1:
            errors.append(f"{candidate_id}: duplicate consolidation decision")
            continue
        review = CandidateReview(
            reviewer=reviewer,
            decided_at=decided_at,
            note=reasoning or None,
        )
        try:
            candidate = candidate_get(candidate_id)
            if candidate is None:
                raise NotFoundError(f"candidate not found: {candidate_id}")
            meta = _consolidation_meta(
                getattr(candidate, "meta", {}) or {},
                action=action,
                reasoning=reasoning,
                decided_at=decided_at,
            )
            apply_decision(
                candidate,
                action=action,
                target_scope=str(target_scope or "").strip(),
                review=review,
                meta=meta,
            )
        except Exception as exc:
            errors.append(f"{candidate_id}: {exc}")
            continue
        counters[action] += 1
        applied_ids.append(candidate_id)

    return {
        "applied_count": sum(counters.values()),
        "promoted_count": counters["promote"],
        "discarded_count": counters["discard"],
        "deferred_count": counters["defer"],
        "applied_candidate_ids": applied_ids,
        "errors": errors,
    }


__all__ = [
    "acquire_consolidation_lock",
    "apply_merge_decisions_via_service",
    "apply_memory_consolidation_decisions",
    "consolidation_lock_key",
    "resolve_consolidation_model_handle",
    "run_consolidation_merge",
]
