import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from openminion.modules.retrieve.schemas import RetrievalFilters


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def build_empty_meta(lane: str, limit_chars: int = 0) -> dict[str, str]:
    return {
        "memory_envelope_version": "v2",
        "memory_envelope_truncated": "false",
        "memory_envelope_truncation_reasons": "",
        "memory_envelope_limit_chars": str(limit_chars),
        "memory_lane": lane,
    }


class RetrievalPipeline:
    def __init__(
        self,
        *,
        retrieve_ctl: Any | None,
        config: Any,
        ranking_config: Any | None,
        logger: logging.Logger,
        agent_id: str,
        retrieval_max_chars: int,
        feedback_boost_on_reference: float = 0.1,
        trace_fn: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        self._retrieve_ctl = retrieve_ctl
        self._config = config
        self._ranking_config = ranking_config
        self._logger = logger
        self._agent_id = str(agent_id or "").strip() or "openminion"
        self._retrieval_max_chars = max(256, int(retrieval_max_chars))
        self._feedback_boost_on_reference = max(0.0, float(feedback_boost_on_reference))
        self._trace_fn = trace_fn

    def sync_runtime_state(
        self,
        *,
        config: Any,
        ranking_config: Any | None,
        retrieve_ctl: Any | None,
        feedback_boost_on_reference: float = 0.1,
        trace_fn: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        self._config = config
        self._ranking_config = ranking_config
        self._retrieve_ctl = retrieve_ctl
        self._feedback_boost_on_reference = max(0.0, float(feedback_boost_on_reference))
        self._trace_fn = trace_fn

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if not callable(self._trace_fn):
            return
        try:
            self._trace_fn(event_type, payload)
        except Exception:
            pass

    def _config_default(self, key: str, fallback: Any) -> Any:
        defaults = getattr(self._config, "defaults", None)
        value = getattr(defaults, key, fallback)
        if value is None:
            return fallback
        if type(value).__module__.startswith("unittest.mock"):
            return fallback
        return value

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    def _extract_hit_text(self, item: dict[str, Any]) -> str:
        text_val: Any = item.get("text", item.get("content", ""))
        if isinstance(text_val, dict):
            text_val = text_val.get("text", text_val.get("content", ""))
        return self._normalize_text(text_val)

    def _merge_and_dedup(self, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                if not isinstance(item, dict):
                    continue
                meta = item.get("meta", {})
                unit_id = ""
                if isinstance(meta, dict):
                    unit_id = str(meta.get("unit_id", "")).strip()
                text_key = self._extract_hit_text(item).lower()
                dedupe_key = unit_id or text_key
                if not dedupe_key or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                merged.append(item)
        return merged

    @staticmethod
    def _item_score(item: Mapping[str, Any]) -> float:
        try:
            return clamp01(float(item.get("score", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _sorted_by_score(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=self._item_score, reverse=True)

    @staticmethod
    def _update_unified_score(item: dict[str, Any], score: float) -> None:
        meta = item.get("meta", {})
        if not isinstance(meta, Mapping):
            return
        score_breakdown = meta.get("score_breakdown", {})
        if not isinstance(score_breakdown, Mapping):
            return
        updated_breakdown = dict(score_breakdown)
        updated_breakdown["unified_score"] = float(score)
        updated_meta = dict(meta)
        updated_meta["score_breakdown"] = updated_breakdown
        item["meta"] = updated_meta

    def _apply_recency_boost(
        self,
        items: list[dict[str, Any]],
        *,
        decay_halflife_days: int,
        recency_weight: float,
    ) -> list[dict[str, Any]]:
        halflife = max(1, int(decay_halflife_days))
        weight = clamp01(recency_weight)
        if weight <= 0.0:
            return list(items)

        now = datetime.now(timezone.utc)
        boosted: list[dict[str, Any]] = []
        for item in items:
            current = dict(item)
            base_score = self._item_score(current)
            created_at = str(current.get("created_at", "") or "").strip()
            recency_multiplier = 1.0
            if created_at:
                try:
                    created_dt = datetime.fromisoformat(created_at)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_days = max(
                        0.0,
                        (now - created_dt.astimezone(timezone.utc)).total_seconds()
                        / 86400.0,
                    )
                    recency_multiplier = 0.5 ** (age_days / float(halflife))
                except ValueError:
                    recency_multiplier = 1.0
            updated_score = clamp01(
                base_score * ((1.0 - weight) + (recency_multiplier * weight))
            )
            current["score"] = updated_score
            self._update_unified_score(current, updated_score)
            boosted.append(current)
        return self._sorted_by_score(boosted)

    def _apply_feedback_boost(
        self,
        items: list[dict[str, Any]],
        *,
        max_boost: float,
    ) -> list[dict[str, Any]]:
        cap = max(0.0, float(max_boost))
        if cap <= 0.0:
            return list(items)
        try:
            hit_divisor = max(
                1.0,
                float(
                    getattr(self._ranking_config, "feedback_hit_divisor", 10.0)
                    if self._ranking_config is not None
                    else 10.0
                ),
            )
        except (TypeError, ValueError):
            hit_divisor = 10.0

        boosted: list[dict[str, Any]] = []
        for item in items:
            current = dict(item)
            base_score = self._item_score(current)
            meta = current.get("meta", {})
            hit_count = 0.0
            if isinstance(meta, Mapping):
                try:
                    hit_count = max(0.0, float(meta.get("hit_count", 0.0) or 0.0))
                except (TypeError, ValueError):
                    hit_count = 0.0
            reuse_signal = clamp01(hit_count / hit_divisor)
            updated_score = clamp01(base_score + min(cap, cap * reuse_signal))
            current["score"] = updated_score
            self._update_unified_score(current, updated_score)
            boosted.append(current)
        return self._sorted_by_score(boosted)

    def _build_retrieve_scope_keys(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        keys = [f"session:{session_id}", f"agent:{agent_id or self._agent_id}"]
        if project_id:
            keys.append(f"project:{project_id}")
        return keys

    def _build_retrieval_filters(
        self,
        *,
        session_id: str,
        agent_id: str,
        project_id: str | None,
        source_types: list[str],
        time_window_hours: int | None,
    ) -> "RetrievalFilters":
        from openminion.modules.retrieve.schemas import RetrievalFilters

        return RetrievalFilters(
            scope_keys=self._build_retrieve_scope_keys(
                session_id=session_id,
                agent_id=agent_id,
                project_id=project_id,
            ),
            types=source_types,
            time_window_hours=time_window_hours,
        )

    def _candidate_similarity(
        self, left: dict[str, Any], right: dict[str, Any]
    ) -> float:
        left_emb = left.get("embedding")
        right_emb = right.get("embedding")
        if (
            isinstance(left_emb, (list, tuple))
            and isinstance(right_emb, (list, tuple))
            and len(left_emb) == len(right_emb)
            and len(left_emb) > 0
        ):
            try:
                dot = sum(float(a) * float(b) for a, b in zip(left_emb, right_emb))
                left_norm = sum(float(a) * float(a) for a in left_emb) ** 0.5
                right_norm = sum(float(b) * float(b) for b in right_emb) ** 0.5
                if left_norm > 0 and right_norm > 0:
                    cosine = dot / (left_norm * right_norm)
                    return clamp01((cosine + 1.0) / 2.0)
            except (TypeError, ValueError):
                pass
        return 0.0

    def mmr_rerank(
        self,
        candidates: list[dict[str, Any]],
        *,
        k: int,
        lambda_: float,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        target_k = max(1, int(k))
        lambda_weight = clamp01(float(lambda_))
        remaining = [dict(item) for item in candidates if isinstance(item, dict)]
        if not remaining:
            return []
        remaining.sort(key=lambda it: float(it.get("score", 0.0) or 0.0), reverse=True)
        selected: list[dict[str, Any]] = [remaining.pop(0)]
        while remaining and len(selected) < target_k:
            best_idx = 0
            best_value = None
            for idx, candidate in enumerate(remaining):
                relevance = clamp01(float(candidate.get("score", 0.0) or 0.0))
                max_similarity = 0.0
                for chosen in selected:
                    max_similarity = max(
                        max_similarity,
                        self._candidate_similarity(candidate, chosen),
                    )
                mmr_value = (lambda_weight * relevance) - (
                    (1.0 - lambda_weight) * max_similarity
                )
                if best_value is None or mmr_value > best_value:
                    best_value = mmr_value
                    best_idx = idx
            selected.append(remaining.pop(best_idx))
        return selected

    def _retrieve_split(
        self,
        retrieve_ctl: Any,
        *,
        query: str,
        session_id: str,
        agent_id: str,
        project_id: str | None,
        k_conversational: int,
        k_knowledge: int,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        conversational_filters = self._build_retrieval_filters(
            session_id=session_id,
            agent_id=agent_id,
            project_id=project_id,
            source_types=["mem", "episode"],
            time_window_hours=168,
        )
        from openminion.modules.retrieve.schemas import RetrievalFilters

        knowledge_filters = RetrievalFilters(
            scope_keys=[],
            types=["skill", "doc", "artifact"],
            time_window_hours=None,
        )

        conversational_hits: list[dict[str, Any]] = []
        knowledge_hits: list[dict[str, Any]] = []

        try:
            raw = retrieve_ctl.retrieve(
                query=query,
                purpose="act",
                scope={"session_id": session_id, "agent_id": agent_id},
                k=max(1, int(k_conversational)),
                strategy="contextual",
                filters=conversational_filters.model_dump(
                    mode="python",
                    exclude_none=True,
                ),
            )
            if isinstance(raw, list):
                conversational_hits = [item for item in raw if isinstance(item, dict)]
        except Exception as exc:
            self._logger.warning(
                "memory.retrieval.retrieve_split conversational failed session_id=%s error=%s",
                session_id,
                exc,
            )
            self._trace(
                "memory.retrieval.retrieve_ctl_error",
                {
                    "session_id": session_id,
                    "lane": "conversational",
                    "error": str(exc),
                },
            )

        try:
            raw = retrieve_ctl.retrieve(
                query=query,
                purpose="act",
                scope={"session_id": session_id, "agent_id": agent_id},
                k=max(1, int(k_knowledge)),
                strategy="auto",
                filters=knowledge_filters.model_dump(
                    mode="python",
                    exclude_none=True,
                ),
            )
            if isinstance(raw, list):
                knowledge_hits = [item for item in raw if isinstance(item, dict)]
        except Exception as exc:
            self._logger.warning(
                "memory.retrieval.retrieve_split knowledge failed session_id=%s error=%s",
                session_id,
                exc,
            )
            self._trace(
                "memory.retrieval.retrieve_ctl_error",
                {
                    "session_id": session_id,
                    "lane": "knowledge",
                    "error": str(exc),
                },
            )

        merged = self._merge_and_dedup(conversational_hits, knowledge_hits)
        return merged, {
            "conversational": len(conversational_hits),
            "knowledge": len(knowledge_hits),
        }

    def _format_retrieval_context(self, items: list[dict[str, Any]]) -> str:
        lines = ["## Memory (dynamic retrieval)"]
        for item in items:
            text = self._extract_hit_text(item)
            if not text:
                continue
            lines.append(f"  • {text}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def rank_and_format(
        self,
        memory_hits: list[dict[str, Any]],
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
        project_id: str | None = None,
    ) -> tuple[str, dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
        limit = max(
            128,
            max_chars if max_chars is not None else self._retrieval_max_chars,
        )
        meta = build_empty_meta("retrieval", limit)

        retrieve_hits, split_counts = self._rank_retrieve_hits(
            user_message=user_message,
            session_id=session_id,
            project_id=project_id,
        )
        merged_hits = self._merge_and_dedup(memory_hits, retrieve_hits)
        if not merged_hits:
            meta["memory_envelope_limit_chars"] = str(limit)
            return "", meta, retrieve_hits, merged_hits

        content = self._format_retrieval_context(merged_hits)
        if len(content) > limit:
            content = content[:limit]
            meta["memory_envelope_truncated"] = "true"
            meta["memory_envelope_truncation_reasons"] = "retrieval_limit"
        meta["memory_envelope_limit_chars"] = str(limit)
        self._trace(
            "memory.retrieval.dual_query",
            {
                "session_id": session_id,
                "memory_hits": len(memory_hits),
                "retrieve_hits": len(retrieve_hits),
                "merged_hits": len(merged_hits),
                "retrieve_ctl_available": str(self._retrieve_ctl is not None).lower(),
                "conversational_hits": split_counts["conversational"],
                "knowledge_hits": split_counts["knowledge"],
            },
        )
        self._trace(
            "memory.retrieval.result",
            {
                "session_id": session_id,
                "query_len": len(user_message),
                "results": len(merged_hits),
                "retrieval_chars": len(content),
            },
        )
        return content, meta, retrieve_hits, merged_hits

    def _rank_retrieve_hits(
        self,
        *,
        user_message: str,
        session_id: str,
        project_id: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        split_counts = {"conversational": 0, "knowledge": 0}
        if self._retrieve_ctl is None:
            return [], split_counts
        try:
            retrieve_hits, split_counts = self._retrieve_split(
                self._retrieve_ctl,
                query=user_message,
                session_id=session_id,
                agent_id=self._agent_id,
                project_id=project_id,
                k_conversational=int(self._config_default("k_conversational", 3)),
                k_knowledge=int(self._config_default("k_knowledge", 3)),
            )
            return self._rerank_retrieve_hits(retrieve_hits), split_counts
        except Exception as exc:
            self._logger.warning(
                "memory.retrieval.retrieve_ctl failed agent_id=%s session_id=%s error=%s",
                self._agent_id,
                session_id,
                exc,
            )
            self._trace(
                "memory.retrieval.retrieve_ctl_error",
                {"session_id": session_id, "error": str(exc)},
            )
            return [], split_counts

    def _rerank_retrieve_hits(
        self,
        retrieve_hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        boosted_hits = self._apply_recency_boost(
            retrieve_hits,
            decay_halflife_days=int(self._config_default("decay_halflife_days", 30)),
            recency_weight=float(self._config_default("recency_weight", 0.3)),
        )
        boosted_hits = self._apply_feedback_boost(
            boosted_hits,
            max_boost=self._feedback_boost_on_reference,
        )
        total_k = max(
            1,
            int(self._config_default("k_conversational", 3))
            + int(self._config_default("k_knowledge", 3)),
        )
        mmr_enabled = bool(
            getattr(self._ranking_config, "mmr_enabled", True)
            if self._ranking_config is not None
            else self._config_default("mmr_enabled", True)
        )
        if not mmr_enabled:
            return boosted_hits[:total_k]
        mmr_lambda = float(
            getattr(self._ranking_config, "mmr_lambda", 0.6)
            if self._ranking_config is not None
            else self._config_default("mmr_lambda", 0.6)
        )
        return self.mmr_rerank(boosted_hits, k=total_k, lambda_=mmr_lambda)



__all__ = [
    "RetrievalPipeline",
    "build_empty_meta",
    "clamp01",
]
