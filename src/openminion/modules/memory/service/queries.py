"""Memory service query operations."""

# mypy: disable-error-code="attr-defined,no-any-return,no-untyped-def,valid-type,misc"

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
import uuid
from typing import Any, Literal

from openminion.modules.memory.contracts.types import MemoryProcedure
from openminion.modules.memory.errors import InvalidArgumentError, NotFoundError
from openminion.modules.memory.models import (
    MemoryRecord,
    MemoryRelation,
    MemoryTierTransition,
    _as_memory_relation_type,
    _as_memory_relation_type_list,
)
from openminion.modules.memory.runtime.scope import (
    assert_scope_matches_agent,
)
from openminion.modules.memory.runtime.scorer import clamp01 as clamp_score
from openminion.modules.memory.storage.base import (
    ListQueryOptions,
    SearchQueryOptions,
    record_matches_namespaces,
)


class MemoryServiceQueryMixin:
    def get(self, record_id: str) -> MemoryRecord:
        record = self._store.get(record_id)
        if not record:
            raise NotFoundError(
                f"Record {record_id!r} not found", details={"id": record_id}
            )
        return record

    def _apply_retrieval_limit(self, requested_limit: int | None) -> int | None:
        decision = self._retrieval_policy.resolve_limit(requested_limit=requested_limit)
        self._record_policy_decision(lane="retrieval", decision=decision)
        limit = getattr(decision, "limit", requested_limit)
        if limit is None:
            return None
        return max(1, int(limit))

    def list(
        self,
        options: ListQueryOptions,
        *,
        agent_id: str | None = None,
    ) -> list[MemoryRecord]:
        if agent_id:
            for scope in options.scopes:
                assert_scope_matches_agent(scope, agent_id)
        started = perf_counter()
        resolved_limit = self._apply_retrieval_limit(options.limit)
        normalized = ListQueryOptions(
            scopes=list(options.scopes),
            types=options.types,
            tiers=options.tiers,
            include_invalidated=bool(getattr(options, "include_invalidated", False)),
            limit=None if options.namespaces else resolved_limit,
            offset=None if options.namespaces else options.offset,
            order_by=options.order_by,
            namespaces=options.namespaces,
        )
        rows = self._store.list(normalized)
        if options.namespaces:
            rows = [
                record
                for record in rows
                if record_matches_namespaces(record, options.namespaces)
            ]
            offset = max(0, int(options.offset or 0))
            rows = rows[offset:]
            if resolved_limit is not None:
                rows = rows[:resolved_limit]
        self._emit_query_metrics(
            session_id=self._resolve_telemetry_session_id(
                scopes=list(normalized.scopes)
            ),
            turn_id=self._resolve_telemetry_turn_id(),
            operation="query",
            result_count=len(rows),
            latency_ms=(perf_counter() - started) * 1000.0,
            token_estimate=len(str(getattr(normalized, "scopes", []) or [])),
            extra={"query_kind": "list"},
        )
        return rows

    def search(
        self,
        options: SearchQueryOptions,
        *,
        agent_id: str | None = None,
    ) -> list[MemoryRecord]:
        if agent_id:
            for scope in options.scopes:
                assert_scope_matches_agent(scope, agent_id)
        started = perf_counter()
        resolved_limit = self._apply_retrieval_limit(options.limit)
        normalized = SearchQueryOptions(
            query=options.query,
            scopes=list(options.scopes),
            types=options.types,
            tiers=options.tiers,
            filters=options.filters,
            include_invalidated=bool(getattr(options, "include_invalidated", False)),
            limit=None if options.namespaces else resolved_limit,
            namespaces=options.namespaces,
        )
        rows = self._store.search(normalized)
        if options.namespaces:
            rows = [
                record
                for record in rows
                if record_matches_namespaces(record, options.namespaces)
            ]
            if resolved_limit is not None:
                rows = rows[:resolved_limit]
        self._emit_query_metrics(
            session_id=self._resolve_telemetry_session_id(
                scopes=list(normalized.scopes)
            ),
            turn_id=self._resolve_telemetry_turn_id(),
            operation="query",
            result_count=len(rows),
            latency_ms=(perf_counter() - started) * 1000.0,
            token_estimate=len(str(normalized.query or "").split()),
            extra={"query_kind": "search"},
        )
        return rows

    def retrieve_by_entities(
        self,
        *,
        entities: list[str],
        scopes: list[str],
        types: list[Any] | None = None,
        limit: int | None = None,
        agent_id: str | None = None,
    ) -> list[MemoryRecord]:
        if agent_id:
            for scope in scopes:
                assert_scope_matches_agent(scope, agent_id)
        started = perf_counter()
        rows = self._store.retrieve_by_entities(
            entities=entities,
            scopes=scopes,
            types=types,
            limit=self._apply_retrieval_limit(limit),
        )
        self._emit_query_metrics(
            session_id=self._resolve_telemetry_session_id(scopes=list(scopes)),
            turn_id=self._resolve_telemetry_turn_id(),
            operation="query",
            result_count=len(rows),
            latency_ms=(perf_counter() - started) * 1000.0,
            token_estimate=sum(len(str(item or "").split()) for item in entities),
            extra={"query_kind": "entities"},
        )
        return rows

    def touch_last_hit(self, record_id: str) -> None:
        handler = getattr(self._store, "touch_last_hit", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "touch_last_hit is unsupported by the configured memory store"
            )
        try:
            handler(record_id)
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc

    def transition_tier(
        self,
        *,
        record_id: str,
        to_tier: str,
        transition_reason: str,
        transition_at: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransition:
        handler = getattr(self._store, "transition_tier", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "transition_tier is unsupported by the configured memory store"
            )
        normalized_at = (
            str(transition_at or "").strip() or datetime.now(timezone.utc).isoformat()
        )
        try:
            return handler(
                record_id,
                to_tier=to_tier,
                transition_reason=transition_reason,
                transition_at=normalized_at,
                meta=meta,
            )
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc

    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryTierTransition]:
        handler = getattr(self._store, "list_tier_transitions", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "list_tier_transitions is unsupported by the configured memory store"
            )
        return handler(record_id=record_id, scopes=scopes, limit=limit)

    def put_tier_transition(
        self,
        transition: MemoryTierTransition,
    ) -> str:
        handler = getattr(self._store, "put_tier_transition", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "put_tier_transition is unsupported by the configured memory store"
            )
        try:
            return handler(transition)
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc

    def reconcile_tiers(
        self,
        *,
        scopes: list[str],
        promotion_age_days: int | None = None,
        reaccess_promote_threshold: int | None = None,
        max_working_access_count: int | None = None,
        tiers: list[str] | None = None,
    ) -> list[MemoryTierTransition]:
        if not scopes:
            return []
        cfg = self._tiering_config
        resolved_promotion_age = int(
            promotion_age_days
            if promotion_age_days is not None
            else getattr(cfg, "promotion_age_days", 30)
        )
        resolved_reaccess_threshold = int(
            reaccess_promote_threshold
            if reaccess_promote_threshold is not None
            else getattr(cfg, "reaccess_promote_threshold", 3)
        )
        resolved_max_working_access = int(
            max_working_access_count
            if max_working_access_count is not None
            else getattr(cfg, "max_working_access_count", 1)
        )
        if not bool(getattr(cfg, "enabled", True)):
            return []
        now = datetime.now(timezone.utc)
        records = self.list(
            ListQueryOptions(
                scopes=list(scopes),
                tiers=tiers or ["working", "archival"],
                order_by=None,
            )
        )
        transitions: list[MemoryTierTransition] = []
        for record in records:
            if (
                record.superseded_by_id
                or record.is_deleted
                or record.is_invalidated_at()
            ):
                continue
            created_at = datetime.fromisoformat(
                str(record.created_at).replace("Z", "+00:00")
            )
            age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
            access_count = int(getattr(record, "access_count", 0) or 0)
            if (
                str(record.tier) == "working"
                and age_days >= float(resolved_promotion_age)
                and access_count <= resolved_max_working_access
            ):
                transitions.append(
                    self.transition_tier(
                        record_id=record.id,
                        to_tier="archival",
                        transition_reason="age_threshold",
                        transition_at=now.isoformat(),
                        meta={
                            "promotion_age_days": resolved_promotion_age,
                            "access_count": access_count,
                        },
                    )
                )
                continue
            if (
                str(record.tier) == "archival"
                and access_count >= resolved_reaccess_threshold
            ):
                transitions.append(
                    self.transition_tier(
                        record_id=record.id,
                        to_tier="working",
                        transition_reason="reaccess_threshold",
                        transition_at=now.isoformat(),
                        meta={
                            "reaccess_promote_threshold": resolved_reaccess_threshold,
                            "access_count": access_count,
                        },
                    )
                )
        return transitions

    def apply_outcome_feedback(
        self,
        *,
        record_ids: list[str],
        outcome: Literal["success", "failed", "timeout"],
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        if outcome not in {"success", "failed", "timeout"}:
            raise InvalidArgumentError(
                f"invalid outcome for attribution: {outcome!r}",
                details={"outcome": outcome},
            )
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for record_id in record_ids:
            normalized = str(record_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_ids.append(normalized)
        if not normalized_ids:
            return 0
        handler = getattr(self._store, "apply_outcome_feedback", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "outcome attribution is unsupported by the configured memory store"
            )
        try:
            return int(
                handler(
                    normalized_ids,
                    outcome=outcome,
                    command_id=str(command_id or "").strip(),
                    observed_at=str(observed_at or "").strip(),
                    feedback_delta=float(feedback_delta),
                )
                or 0
            )
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc

    def search_semantic(
        self,
        query: str,
        scopes: list[str],
        *,
        types: list[Any] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        started = perf_counter()
        token_estimate = len(str(query or "").strip().split())

        def _is_live_record(record: MemoryRecord | None) -> bool:
            if record is None:
                return False
            if bool(getattr(record, "is_deleted", False)):
                return False
            if bool(getattr(record, "valid_to", None)) and record.is_invalidated_at():
                return False
            return not bool(getattr(record, "superseded_by_id", None))

        def _fallback(reason: str) -> list[MemoryRecord]:
            rows = self.search(
                SearchQueryOptions(
                    query=normalized_query,
                    scopes=normalized_scopes,
                    types=types,
                    limit=resolved_limit,
                )
            )
            self._emit_query_metrics(
                session_id=self._resolve_telemetry_session_id(scopes=normalized_scopes),
                turn_id=self._resolve_telemetry_turn_id(),
                operation="fallback",
                result_count=len(rows),
                latency_ms=(perf_counter() - started) * 1000.0,
                token_estimate=token_estimate,
                extra={"reason": reason},
            )
            return rows

        resolved_limit = self._apply_retrieval_limit(limit)
        normalized_query = str(query or "").strip()
        normalized_scopes = [
            str(scope or "").strip() for scope in scopes if str(scope or "").strip()
        ]
        if not normalized_query or not normalized_scopes:
            return []
        if self._vector_adapter is None:
            return _fallback("vector_adapter_missing")
        try:
            vector_map: dict[str, float] = {}
            vector_results: list[tuple[str, float, dict[str, Any] | None]] = []
            for scope in normalized_scopes:
                scoped_results = self._vector_adapter.search(
                    query=normalized_query,
                    top_k=resolved_limit or 10,
                    filters={"scope": scope},
                )
                for record_id, score, meta in scoped_results or []:
                    vector_results.append((record_id, score, meta))
                    record_key = str(record_id)
                    current = vector_map.get(record_key)
                    if current is None or float(score or 0.0) > current:
                        vector_map[record_key] = float(score or 0.0)
            if not vector_results:
                return _fallback("vector_results_empty")

            keyword_results = self.search(
                SearchQueryOptions(
                    query=normalized_query,
                    scopes=normalized_scopes,
                    types=types,
                    limit=resolved_limit,
                )
            )

            bm25_weight = clamp_score(
                float(getattr(self._ranking_config, "semantic_bm25_weight", 0.5))
            )
            vector_weight = 1.0 - bm25_weight
            blended: list[tuple[MemoryRecord, float]] = []
            seen: set[str] = set()
            for record in keyword_results:
                if record.id in seen:
                    continue
                seen.add(record.id)
                bm25_score = 0.0
                record_meta = getattr(record, "meta", {}) or {}
                if isinstance(record_meta, dict):
                    raw_bm25 = record_meta.get("bm25_score")
                    if raw_bm25 is not None:
                        try:
                            bm25_score = float(raw_bm25)
                        except (TypeError, ValueError):
                            bm25_score = 0.0
                bm25_score = clamp_score(float(bm25_score))
                blended_score = (bm25_score * bm25_weight) + (
                    clamp_score(vector_map.get(record.id, 0.0)) * vector_weight
                )
                blended.append((record, blended_score))

            for record_id, vector_score in vector_map.items():
                if record_id in seen:
                    continue
                record = self._store.get(record_id)
                if not _is_live_record(record):
                    continue
                seen.add(record_id)
                blended_score = clamp_score(vector_score) * vector_weight
                blended.append((record, blended_score))

            blended.sort(key=lambda item: item[1], reverse=True)
            output_limit = resolved_limit if resolved_limit is not None else 10
            results = [record for record, _score in blended[: max(1, output_limit)]]
            session_id = self._resolve_telemetry_session_id(scopes=normalized_scopes)
            turn_id = self._resolve_telemetry_turn_id()
            self._emit_query_metrics(
                session_id=session_id,
                turn_id=turn_id,
                operation="query",
                result_count=len(results),
                latency_ms=(perf_counter() - started) * 1000.0,
                token_estimate=token_estimate,
                extra={"query_kind": "semantic"},
            )
            self._emit_query_metrics(
                session_id=session_id,
                turn_id=turn_id,
                operation="rerank",
                result_count=len(results),
                latency_ms=(perf_counter() - started) * 1000.0,
                token_estimate=token_estimate,
                extra={"query_kind": "semantic"},
            )
            return results
        except Exception:
            return _fallback("semantic_error")

    def search_all(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        types: list[Any] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_scopes = (
            [str(scope or "").strip() for scope in scopes if str(scope or "").strip()]
            if scopes is not None
            else self._store.list_scopes()
        )
        normalized_scopes = [scope for scope in normalized_scopes if scope]
        if not normalized_scopes:
            return []
        hits = self.search(
            SearchQueryOptions(
                query=normalized_query,
                scopes=normalized_scopes,
                types=types,
                limit=limit,
            )
        )
        threshold = max(0.0, float(min_confidence))
        return [
            record
            for record in hits
            if float(getattr(record, "confidence", 0.0) or 0.0) >= threshold
        ]

    def put_relation(
        self,
        *,
        source_record_id: str,
        target_record_id: str,
        relation_type: str,
        meta: dict[str, Any] | None = None,
    ) -> str:
        relation = MemoryRelation(
            relation_id=f"rel_{uuid.uuid4().hex[:12]}",
            source_record_id=str(source_record_id or "").strip(),
            target_record_id=str(target_record_id or "").strip(),
            relation_type=_as_memory_relation_type(relation_type),
            created_at=datetime.now(timezone.utc).isoformat(),
            meta=dict(meta or {}),
        )
        return self._store.put_relation(relation)

    def list_relations(
        self,
        *,
        record_id: str,
        relation_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRelation]:
        return self._store.list_relations(
            str(record_id or "").strip(),
            relation_types=_as_memory_relation_type_list(relation_types),
            limit=limit,
        )

    def get_related_records(
        self,
        *,
        record_id: str,
        scopes: list[str],
        relation_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        return self._store.get_related_records(
            str(record_id or "").strip(),
            list(scopes),
            relation_types=_as_memory_relation_type_list(relation_types),
            limit=self._apply_retrieval_limit(limit),
        )

    def search_with_relations(
        self,
        *,
        query: str,
        scopes: list[str],
        types: list[Any] | None = None,
        relation_types: list[str] | None = None,
        limit: int | None = None,
        related_limit: int = 5,
    ) -> list[MemoryRecord]:
        base_hits = self.search(
            SearchQueryOptions(
                query=str(query or "").strip(),
                scopes=list(scopes),
                types=types,
                limit=limit,
            )
        )
        merged: list[MemoryRecord] = []
        seen: set[str] = set()
        for record in base_hits:
            if record.id not in seen:
                merged.append(record)
                seen.add(record.id)
            related = self.get_related_records(
                record_id=record.id,
                scopes=scopes,
                relation_types=relation_types,
                limit=related_limit,
            )
            for neighbor in related:
                if neighbor.id in seen:
                    continue
                merged.append(neighbor)
                seen.add(neighbor.id)
        if limit is None:
            return merged
        return merged[: max(1, int(limit))]

    def should_refresh_capsule(
        self,
        *,
        strategy: str,
        has_cached_capsule: bool,
        memory_changed: bool,
    ):
        decision = self._capsule_refresh_policy.should_refresh(
            strategy=strategy,
            has_cached_capsule=has_cached_capsule,
            memory_changed=memory_changed,
        )
        self._record_policy_decision(lane="capsule_refresh", decision=decision)
        return decision

    def should_collect_retention(
        self,
        *,
        gc_enabled: bool,
        pending_records: int,
    ):
        decision = self._retention_policy.should_collect_garbage(
            gc_enabled=gc_enabled,
            pending_records=pending_records,
        )
        self._record_policy_decision(lane="retention", decision=decision)
        return decision

    def get_procedure(self, *, procedure_id: str) -> MemoryProcedure | None:
        normalized_id = str(procedure_id or "").strip()
        if not normalized_id:
            return None
        try:
            record = self._store.get(normalized_id)
        except Exception:
            return None
        if not record or getattr(record, "type", "") != "procedure":
            return None
        return _project_record_to_memory_procedure(record)


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _project_record_to_memory_procedure(record: MemoryRecord) -> MemoryProcedure:
    content = record.content
    if isinstance(content, dict):
        steps = _coerce_str_list(content.get("steps"))
        preflight = _coerce_str_list(content.get("preflight"))
        rollback_hint = str(content.get("rollback_hint") or "").strip()
        if not steps:
            body = content.get("body") or content.get("text") or ""
            steps = _coerce_str_list(body)
    else:
        steps = _coerce_str_list(content)
        preflight = []
        rollback_hint = ""
    return MemoryProcedure(
        procedure_id=record.id,
        title=(record.title or "").strip(),
        steps=steps,
        preflight=preflight,
        rollback_hint=rollback_hint,
    )
