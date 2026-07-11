from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from openminion.modules.memory.audit import TrustGateEvent, emit_trust_gate_event
from openminion.modules.memory.trust.rate_limit import (
    PromotionRateLimiter,
    RateLimitDecision,
)
from openminion.modules.memory.trust.scorer import compute_trust_score
from openminion.modules.memory.runtime.candidate_readiness import (
    score_candidate_from_config,
)
from openminion.modules.memory.models import (
    CandidateReview,
    MemoryCandidate,
    MemoryType,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    RecordOrder,
)
from openminion.modules.memory.runtime.scorer import score_records
from openminion.modules.memory.runtime.config_values import coerce_float
from openminion.modules.memory.runtime.extraction.records import _content_text
from openminion.modules.memory.runtime.extraction.text import (
    _normalize_fact_key,
    _tokenize_text,
)


def _normalized_value_equal(left: str, right: str) -> bool:
    """VKCR-02 spec D2: typed value equality for same-key records."""

    def _normalize(text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())

    return _normalize(left) == _normalize(right)


def _candidate_source_class(candidate: Any) -> str | None:
    raw = getattr(candidate, "source_class", None)
    if raw is None:
        meta = dict(getattr(candidate, "meta", {}) or {})
        raw = meta.get("source_class")
    value = str(raw or "").strip()
    if value in {
        "user_input",
        "tool_result",
        "llm_extracted",
        "agent_inferred",
        "imported_bundle",
    }:
        return value
    return None


def _trust_gate_meta(
    *,
    candidate: Any,
    trust_result: Any,
    source_window: RateLimitDecision,
    gate_reason_code: str,
    gate_allowed: bool,
    readiness_after_trust: float,
) -> dict[str, Any]:
    meta = dict(getattr(candidate, "meta", {}) or {})
    meta.update(
        {
            "trust_gate_allowed": bool(gate_allowed),
            "trust_gate_reason_code": str(gate_reason_code or ""),
            "trust_score": float(trust_result.trust_score.score),
            "trust_peer_count": int(trust_result.peer_count),
            "trust_source_class": trust_result.source_class,
            "promotion_readiness_after_trust": float(readiness_after_trust),
            "trust_sub_signals": {
                "source_provenance": float(trust_result.trust_score.source_provenance),
                "corroboration": float(trust_result.trust_score.corroboration),
                "contradiction_penalty": float(
                    trust_result.trust_score.contradiction_penalty
                ),
                "rate_limit_pressure": float(
                    trust_result.trust_score.rate_limit_pressure
                ),
            },
        }
    )
    if source_window.retry_after_ms is not None:
        meta["trust_gate_retry_after_ms"] = int(source_window.retry_after_ms)
    else:
        meta.pop("trust_gate_retry_after_ms", None)
    return meta


class LearningMixin:
    def _scope_for_durable_record(self, record_type: MemoryType) -> str:
        if record_type == "project_convention" and self._project_id:
            return f"project:{self._project_id}"
        return f"agent:{self._agent_id}"

    def _rerank_long_term_records(
        self,
        records: list[Any],
        *,
        use_search_scores: bool,
    ) -> list[Any]:
        if not records:
            return records
        query_scores: list[float | None] | None = None
        if use_search_scores:
            query_scores = []
            for record in records:
                meta = getattr(record, "meta", {}) or {}
                score = None
                if isinstance(meta, Mapping):
                    try:
                        score = float(meta.get("bm25_score", 0.0))
                    except (TypeError, ValueError):
                        score = 0.0
                query_scores.append(score)
        return score_records(
            records,
            ranking_config=self._ranking_config,
            query_bm25_scores=query_scores,
        )

    def _list_proposed_candidates(self, *, limit: int = 100) -> list[MemoryCandidate]:
        return self._service.candidate_list(
            CandidateListOptions(
                proposed_scope=f"agent:{self._agent_id}",
                status="proposed",
                limit=limit,
            )
        )

    def _reject_candidate(self, candidate: MemoryCandidate, *, note: str) -> None:
        self._service.candidate_update(
            candidate.candidate_id,
            {
                "status": "rejected",
                "review": CandidateReview(
                    reviewer="candidate_learning",
                    decided_at=datetime.now(timezone.utc).isoformat(),
                    note=note,
                ),
            },
        )

    def _gc_candidates(self) -> int:
        max_age_days = int(
            getattr(self._candidate_learning_config, "candidate_max_age_days", 30)
        )
        threshold = float(
            getattr(
                self._candidate_learning_config,
                "promotion_readiness_threshold",
                0.6,
            )
        )
        now = datetime.now(timezone.utc)
        rejected = 0
        for candidate in self._list_proposed_candidates():
            created_at = getattr(candidate, "created_at", None)
            if not created_at:
                continue
            try:
                created_dt = datetime.fromisoformat(str(created_at))
            except ValueError:
                continue
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created_dt).total_seconds() / 86400.0)
            if age_days < max_age_days:
                continue
            readiness = score_candidate_from_config(
                candidate,
                config=self._candidate_learning_config,
                now=now,
            )
            if readiness >= threshold:
                continue
            self._reject_candidate(candidate, note="gc_expired")
            rejected += 1
        return rejected

    # the following were removed as the deepest remaining

    def _candidate_promotion_gate(
        self,
        *,
        candidate: MemoryCandidate,
        effective_now: datetime,
        threshold: float,
        min_trust: float,
        active_rate_limiter: PromotionRateLimiter,
    ) -> tuple[bool, str | None]:
        readiness = score_candidate_from_config(
            candidate,
            config=self._candidate_learning_config,
            now=effective_now,
        )
        if readiness < threshold:
            if bool(dict(getattr(candidate, "meta", {}) or {}).get("contradicted")):
                self._reject_candidate(candidate, note="contradicted_before_promotion")
            return False, None
        source_class = _candidate_source_class(candidate)
        source_window = (
            active_rate_limiter.assess(source_class, at=effective_now)
            if source_class is not None
            else RateLimitDecision(True, "ALLOWED", 0, None)
        )
        trust_result = compute_trust_score(
            candidate,
            repo=self._service,
            source_window=source_window,
        )
        readiness_after_trust = score_candidate_from_config(
            candidate,
            config=self._candidate_learning_config,
            now=effective_now,
            trust_score=trust_result.trust_score.score,
        )
        gate_reason_code = "ALLOWED"
        gate_allowed = True
        if not source_window.allowed:
            gate_reason_code, gate_allowed = source_window.reason_code, False
        elif trust_result.reason_code != "ALLOWED":
            gate_reason_code, gate_allowed = trust_result.reason_code, False
        elif (
            trust_result.trust_score.score < min_trust
            or readiness_after_trust < threshold
        ):
            gate_reason_code, gate_allowed = "BELOW_TRUST_THRESHOLD", False
        trust_meta = _trust_gate_meta(
            candidate=candidate,
            trust_result=trust_result,
            source_window=source_window,
            gate_reason_code=gate_reason_code,
            gate_allowed=gate_allowed,
            readiness_after_trust=readiness_after_trust,
        )
        self._service.candidate_update(candidate.candidate_id, {"meta": trust_meta})
        emit_trust_gate_event(
            self._service,
            TrustGateEvent(
                candidate_id=str(candidate.candidate_id),
                claim_key=getattr(candidate, "claim_key", None),
                trust_score=float(trust_result.trust_score.score),
                sub_signals=dict(trust_meta["trust_sub_signals"]),
                decision="ALLOWED" if gate_allowed else "BLOCKED",
                reason_code=str(gate_reason_code),
                scope=str(getattr(candidate, "proposed_scope", "") or "") or None,
                record_type=str(getattr(candidate, "type", "") or "") or None,
                session_id=str(getattr(candidate, "session_id", "") or "") or None,
                retry_after_ms=source_window.retry_after_ms,
            ),
        )
        return gate_allowed, source_class

    def _promote_same_value_candidate(
        self,
        *,
        candidate: MemoryCandidate,
        source_class: str | None,
        effective_now: datetime,
        active_rate_limiter: PromotionRateLimiter,
    ) -> bool:
        candidate_key = str(getattr(candidate, "key", "") or "").strip()
        candidate_scope = f"agent:{self._agent_id}"
        candidate_type = str(getattr(candidate, "type", "") or "").strip()
        candidate_content = _content_text(getattr(candidate, "content", ""))
        if not candidate_key or not candidate_type:
            return False
        existing = self._service.find_record_by_normalized_key(
            scope=candidate_scope,
            record_type=candidate_type,
            normalized_key=candidate_key,
        )
        if existing is None:
            return False
        if not _normalized_value_equal(
            candidate_content,
            _content_text(getattr(existing, "content", "")),
        ):
            return False
        if source_class is not None:
            active_rate_limiter.record(source_class, at=effective_now)
        self._service.candidate_update(
            candidate.candidate_id,
            {
                "status": "approved",
                "confidence": float(getattr(candidate, "confidence", 0.75) or 0.75),
            },
        )
        self._service.reinforce_record(record_id=str(existing.id))
        self._service.candidate_update(candidate.candidate_id, {"status": "promoted"})
        self._trace(
            "memory.vkcr_same_value_reinforcement",
            {
                "record_id": str(existing.id),
                "normalized_key": candidate_key,
                "scope": candidate_scope,
                "type": candidate_type,
            },
        )
        return True

    def _promote_candidate_record(
        self,
        *,
        candidate: MemoryCandidate,
        source_class: str | None,
        effective_now: datetime,
        active_rate_limiter: PromotionRateLimiter,
    ) -> None:
        if source_class is not None:
            active_rate_limiter.record(source_class, at=effective_now)
        self._service.candidate_update(
            candidate.candidate_id,
            {
                "status": "approved",
                "confidence": float(getattr(candidate, "confidence", 0.75) or 0.75),
            },
        )
        self._service.promote_candidate(
            candidate.candidate_id, f"agent:{self._agent_id}"
        )

    def _promote_mature_candidates(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str,
        now: datetime | None = None,
        rate_limiter: PromotionRateLimiter | None = None,
    ) -> int:
        del session_id, user_message, assistant_message
        promoted = 0
        threshold = float(
            getattr(
                self._candidate_learning_config, "promotion_readiness_threshold", 0.6
            )
        )
        min_trust = float(
            getattr(self._candidate_learning_config, "min_trust_for_promotion", 0.5)
        )
        effective_now = now or datetime.now(timezone.utc)
        active_rate_limiter = rate_limiter or PromotionRateLimiter()
        for candidate in self._list_proposed_candidates():
            gate_allowed, source_class = self._candidate_promotion_gate(
                candidate=candidate,
                effective_now=effective_now,
                threshold=threshold,
                min_trust=min_trust,
                active_rate_limiter=active_rate_limiter,
            )
            if not gate_allowed:
                continue
            if self._promote_same_value_candidate(
                candidate=candidate,
                source_class=source_class,
                effective_now=effective_now,
                active_rate_limiter=active_rate_limiter,
            ):
                promoted += 1
                continue
            self._promote_candidate_record(
                candidate=candidate,
                source_class=source_class,
                effective_now=effective_now,
                active_rate_limiter=active_rate_limiter,
            )
            promoted += 1
        return promoted

    def _reflection_summary_window_limit(self) -> int:
        return max(10, int(self._reflection_interval_sessions) * 3)

    def _list_reflection_window_summaries(self) -> list[Any]:
        return self._service.list(
            ListQueryOptions(
                scopes=[f"agent:{self._agent_id}"],
                types=["session_summary"],
                limit=self._reflection_summary_window_limit(),
                order_by=RecordOrder.UPDATED_AT_DESC,
            )
        )

    def _collect_reflection_summary_signals(
        self,
        session_summaries: list[Any],
    ) -> tuple[Counter[str], Counter[str], dict[str, dict[str, Any]]]:
        topic_counts: Counter[str] = Counter()
        correction_counts: Counter[str] = Counter()
        # typed `preference_examples` replaces the
        stable_preferences: dict[str, dict[str, Any]] = {}
        for record in session_summaries:
            content = getattr(record, "content", {}) or {}
            if not isinstance(content, dict):
                continue
            for keyword in content.get("topic_keywords", []) or []:
                normalized = str(keyword).strip().lower()
                if normalized:
                    topic_counts[normalized] += 1
            for correction in content.get("corrections", []) or []:
                normalized = " ".join(str(correction or "").split())[:120].lower()
                if normalized:
                    correction_counts[normalized] += 1
            for example in content.get("preference_examples", []) or []:
                if not isinstance(example, dict):
                    continue
                topic = str(example.get("topic", "") or "").strip().lower()
                key = str(example.get("key", "") or "").strip()
                if not topic or not key:
                    continue
                topic_slug = key.split(":", 1)[-1] if ":" in key else topic
                entry = stable_preferences.setdefault(
                    topic_slug,
                    {"topic": topic, "count": 0, "example": {}},
                )
                entry["count"] = int(entry.get("count", 0) or 0) + 1
                if not entry["example"]:
                    entry["example"] = dict(example)
        return topic_counts, correction_counts, stable_preferences

    def _list_reflection_insights(
        self,
        *,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        insights = self._service.list(
            ListQueryOptions(
                scopes=[f"agent:{self._agent_id}"],
                types=["meta_insight"],
                limit=limit,
                order_by=RecordOrder.UPDATED_AT_DESC,
            )
        )
        normalized_tag = str(tag or "").strip().lower()
        if not normalized_tag:
            return insights
        return [
            record
            for record in insights
            if normalized_tag
            in {
                str(item).strip().lower()
                for item in getattr(record, "tags", []) or []
                if str(item).strip()
            }
        ]

    # `_promote_correction_insights` was removed in full. The

    def _find_matching_user_preference(
        self,
        *,
        insight: Any,
        example: dict[str, Any],
    ) -> Any | None:
        # the previous implementation fell back
        del insight  # unused: was only consumed to derive prose fallback
        target_key = str(example.get("key", "") or "").strip()
        if not target_key:
            return None
        candidates = self._service.list(
            ListQueryOptions(
                scopes=[f"agent:{self._agent_id}"],
                types=["user_preference"],
                limit=100,
                order_by=RecordOrder.UPDATED_AT_DESC,
            )
        )
        for record in candidates:
            record_key = str(getattr(record, "key", "") or "").strip()
            if record_key == target_key:
                return record
        return None

    def _boost_matching_user_preference(
        self,
        *,
        insight: Any,
        insight_key: str,
        topic_slug: str,
        example: dict[str, Any],
        meta: dict[str, Any],
        now: datetime,
    ) -> bool:
        match = self._find_matching_user_preference(insight=insight, example=example)
        if match is None:
            return False
        old_confidence = float(getattr(match, "confidence", 0.0) or 0.0)
        new_confidence = min(
            0.95, old_confidence + float(self._preference_stability_boost)
        )
        target_key = str(getattr(match, "key", "") or topic_slug)
        self._service.upsert_record(
            scope=str(getattr(match, "scope", "") or f"agent:{self._agent_id}"),
            record_type="user_preference",
            key=target_key,
            record_patch={
                "title": getattr(match, "title", None),
                "content": getattr(match, "content", ""),
                "tags": list(getattr(match, "tags", []) or []),
                "entities": list(getattr(match, "entities", []) or []),
                "source": getattr(match, "source", "agent_inferred"),
                "confidence": new_confidence,
                "meta": dict(getattr(match, "meta", {}) or {}),
            },
        )
        self._service.upsert_record(
            scope=f"agent:{self._agent_id}",
            record_type="meta_insight",
            key=insight_key,
            record_patch={
                "title": getattr(insight, "title", None),
                "content": getattr(insight, "content", ""),
                "tags": list(getattr(insight, "tags", []) or []),
                "entities": list(getattr(insight, "entities", []) or []),
                "source": getattr(insight, "source", "agent_inferred"),
                "confidence": getattr(insight, "confidence", 0.7),
                "meta": {
                    **meta,
                    "promotion_status": "boosted",
                    "boosted_at": now.isoformat(),
                    "boosted_record_key": target_key,
                },
            },
        )
        self._trace(
            "memory.reflection.preference_boosted",
            {
                "insight_key": insight_key,
                "target_record_key": target_key,
                "old_confidence": old_confidence,
                "new_confidence": new_confidence,
            },
        )
        return True

    def _promote_stable_preference_from_insight(
        self,
        *,
        insight: Any,
        insight_key: str,
        topic_slug: str,
        signal: dict[str, Any],
        example: dict[str, Any],
        meta: dict[str, Any],
    ) -> None:
        example_content = str(
            example.get("content", "")
            or getattr(insight, "content", "")
            or f"Preference memory: {signal.get('topic', topic_slug)}"
        )
        example_key = str(
            example.get("key", "")
            or _normalize_fact_key("pref", str(signal.get("topic", topic_slug)))
        )
        new_confidence = min(
            0.95,
            max(0.75, float(getattr(insight, "confidence", 0.75) or 0.75)),
        )
        promoted_record = self._service.upsert_record(
            scope=f"agent:{self._agent_id}",
            record_type="user_preference",
            key=example_key,
            record_patch={
                "title": str(
                    example.get("title", "")
                    or getattr(insight, "title", "")
                    or "Stable preference"
                ),
                "content": example_content,
                "tags": list(example.get("tags", []) or ["preference"]),
                "entities": sorted(_tokenize_text(example_content)),
                "source": "agent_inferred",
                "confidence": new_confidence,
                "meta": {
                    **meta,
                    "promoted_from_insight_id": str(getattr(insight, "id", "") or ""),
                },
            },
        )
        self._service._store.delete(str(getattr(insight, "id", "") or ""))
        self._trace(
            "memory.reflection.preference_boosted",
            {
                "insight_key": insight_key,
                "target_record_key": str(
                    getattr(promoted_record, "key", "") or example_key
                ),
                "old_confidence": 0.0,
                "new_confidence": new_confidence,
            },
        )

    def _boost_stable_preferences(
        self,
        *,
        session_summaries: list[Any] | None = None,
    ) -> int:
        summaries = session_summaries or self._list_reflection_window_summaries()
        _topic_counts, _correction_counts, stable_preferences = (
            self._collect_reflection_summary_signals(summaries)
        )
        updated = 0
        cooldown_days = max(
            1.0,
            float(self._reflection_interval_sessions)
            * float(self._reboost_cooldown_multiplier),
        )
        now = datetime.now(timezone.utc)
        insights = sorted(
            self._list_reflection_insights(tag="stable_preference"),
            key=lambda record: float(getattr(record, "confidence", 0.0) or 0.0),
            reverse=True,
        )
        for insight in insights:
            if updated >= int(self._max_preference_boosts_per_run):
                break
            meta = dict(getattr(insight, "meta", {}) or {})
            boosted_at_raw = str(meta.get("boosted_at", "") or "").strip()
            if boosted_at_raw:
                try:
                    boosted_at = datetime.fromisoformat(boosted_at_raw)
                    if boosted_at.tzinfo is None:
                        boosted_at = boosted_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    boosted_at = None
                if boosted_at is not None and (
                    now - boosted_at.astimezone(timezone.utc)
                ) < timedelta(days=cooldown_days):
                    continue

            insight_key = str(getattr(insight, "key", "") or "").strip()
            topic_slug = insight_key.split(":", 1)[-1]
            signal = stable_preferences.get(topic_slug)
            if not signal or int(signal.get("count", 0) or 0) < int(
                self._preference_stability_min_sessions
            ):
                continue
            example = dict(signal.get("example", {}) or {})
            if self._boost_matching_user_preference(
                insight=insight,
                insight_key=insight_key,
                topic_slug=topic_slug,
                example=example,
                meta=meta,
                now=now,
            ):
                updated += 1
                continue
            self._promote_stable_preference_from_insight(
                insight=insight,
                insight_key=insight_key,
                topic_slug=topic_slug,
                signal=dict(signal),
                example=example,
                meta=meta,
            )
            updated += 1
        return updated

    def _run_reflection(self, session_summaries: list[Any]) -> list[dict[str, Any]]:
        insights: list[dict[str, Any]] = []
        topic_counts, correction_counts, stable_preferences = (
            self._collect_reflection_summary_signals(session_summaries)
        )
        for keyword, count in topic_counts.items():
            if count < 3:
                continue
            insights.append(
                {
                    "key": _normalize_fact_key("insight-topic", keyword),
                    "title": f"Recurring topic: {keyword}",
                    "content": f"Across recent sessions, {keyword} has come up repeatedly.",
                    "confidence": min(0.9, 0.55 + (0.05 * count)),
                    "tags": ["meta_insight", "recurring_topic", keyword],
                }
            )
        for correction, count in correction_counts.items():
            if count < 2:
                continue
            excerpt = correction[:80]
            insights.append(
                {
                    "key": _normalize_fact_key("insight-correction", excerpt),
                    "title": f"Recurring correction: {excerpt[:48]}",
                    "content": f"The same correction has repeated across sessions: {excerpt}",
                    "confidence": min(0.9, 0.65 + (0.05 * count)),
                    "tags": ["meta_insight", "recurring_correction"],
                }
            )
        for topic_slug, payload in stable_preferences.items():
            count = int(payload.get("count", 0) or 0)
            if count < 3:
                continue
            topic = str(payload.get("topic", "") or topic_slug)
            example = dict(payload.get("example", {}) or {})
            value = str(example.get("title", "") or example.get("content", "") or topic)
            insights.append(
                {
                    "key": _normalize_fact_key("insight-preference", topic_slug),
                    "title": f"Stable preference: {topic}",
                    "content": f"Across recent sessions, the user consistently prefers {value}.",
                    "confidence": min(0.95, 0.75 + (0.03 * count)),
                    "tags": ["meta_insight", "stable_preference", topic],
                }
            )
        return insights

    def _maybe_run_reflection(self) -> int:
        if not self._reflection_enabled:
            return 0
        summaries = self._list_reflection_window_summaries()
        if len(summaries) < self._reflection_interval_sessions:
            return 0
        if len(summaries) % self._reflection_interval_sessions != 0:
            return 0
        existing_insights = self._list_reflection_insights(limit=100)
        existing_keys = {
            str(getattr(record, "key", "") or "").strip()
            for record in existing_insights
            if str(getattr(record, "key", "") or "").strip()
        }
        existing_signatures = {
            (
                str(getattr(record, "title", "") or "").strip().lower(),
                _content_text(getattr(record, "content", "")).lower(),
            )
            for record in existing_insights
        }
        written = 0
        generated = sorted(
            self._run_reflection(summaries),
            key=lambda item: float(item.get("confidence", 0.0) or 0.0),
            reverse=True,
        )
        for insight in generated[: max(1, int(self._max_insights_per_reflection))]:
            key = str(
                insight.get("key", "")
                or _normalize_fact_key("insight", str(insight.get("title", "")))
            ).strip()
            title = str(insight.get("title", "") or "Meta insight").strip()
            content = str(insight.get("content", "") or "").strip()
            tags = list(insight.get("tags", []))
            signature = (title.lower(), content.lower())
            if not content or key in existing_keys or signature in existing_signatures:
                continue
            upserted = self._service.upsert_record(
                scope=f"agent:{self._agent_id}",
                record_type="meta_insight",
                key=key,
                record_patch={
                    "title": title,
                    "content": content,
                    "tags": tags,
                    "entities": sorted(_tokenize_text(content)),
                    "source": "agent_inferred",
                    "confidence": coerce_float(insight.get("confidence", 0.7), 0.7),
                },
            )
            # the reflection insight-write `_detect_contradiction`
            written += 1
            existing_insights.append(upserted)
            existing_keys.add(key)
            existing_signatures.add(signature)
        # the `_promote_correction_insights(session_summaries=...)`
        boosted_preferences = 0
        if self._promotion_enabled:
            boosted_preferences = self._boost_stable_preferences(
                session_summaries=summaries
            )
        if written or boosted_preferences:
            self._trace(
                "memory.reflection.completed",
                {
                    "agent_id": self._agent_id,
                    "session_summary_count": len(summaries),
                    "insights_written": written,
                    "preferences_boosted": boosted_preferences,
                },
            )
        return written


__all__ = ["LearningMixin"]
