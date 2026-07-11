import logging
from typing import Any, Mapping

from openminion.base.time import utc_now_iso
from openminion.modules.memory import default_provenance_recorder
from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)
from openminion.modules.memory.models import MemoryScope
from openminion.modules.memory.storage.base import (
    ListQueryOptions,
    RecordOrder,
    SearchQueryOptions,
)
from openminion.modules.memory.runtime.extraction.records import (
    _format_records_as_context,
)
from openminion.modules.memory.runtime.retrieval_pipeline import (
    build_empty_meta,
)

_logger = logging.getLogger(__name__)


class ContextBuildersMixin:
    def _is_current_session_summary_record(
        self, record: Any, *, session_id: str
    ) -> bool:
        if str(getattr(record, "type", "") or "") != "session_summary":
            return False
        if str(getattr(record, "key", "") or "").strip() == (
            f"session_summary:{session_id}"
        ):
            return True
        tags = getattr(record, "tags", []) or []
        if isinstance(tags, list):
            for tag in tags:
                if str(tag or "").strip() == session_id:
                    return True
        meta = getattr(record, "meta", {}) or {}
        if isinstance(meta, Mapping):
            return str(meta.get("session_id", "") or "").strip() == session_id
        return False

    def _filter_retrievable_records(self, records: list[Any]) -> list[Any]:
        threshold = float(getattr(self, "_retrieval_min_confidence", 0.0) or 0.0)
        if threshold <= 0.0:
            return list(records)
        filtered: list[Any] = []
        for record in records:
            scope = str(getattr(record, "scope", "") or "")
            record_type = str(getattr(record, "type", "") or "")
            try:
                parsed_scope = MemoryScope.parse(scope)
            except ValueError:
                parsed_scope = None
            if (
                parsed_scope is not None and parsed_scope.is_session
            ) or record_type in {
                "session_summary",
                "pin",
            }:
                filtered.append(record)
                continue
            try:
                confidence = float(getattr(record, "confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence >= threshold:
                filtered.append(record)
        return filtered

    def _prioritize_structured_retrieval_records(self, records: list[Any]) -> list[Any]:
        structured: list[Any] = []
        historical_summaries: list[Any] = []
        for record in records:
            if str(getattr(record, "type", "") or "") == "session_summary":
                historical_summaries.append(record)
            else:
                structured.append(record)
        return structured + historical_summaries

    def _record_turn_provenance_trace(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        merged_hits: list[dict[str, Any]],
    ) -> None:
        """Persist post-rank memory hits into the turn provenance trace."""

        entries: list[MemoryProvenanceEntry] = []
        seen_record_ids: set[str] = set()
        for hit in merged_hits:
            hit_meta = hit.get("meta", {}) if isinstance(hit, dict) else {}
            if not isinstance(hit_meta, Mapping):
                continue
            record_id = str(hit_meta.get("record_id", "") or "").strip()
            if not record_id or record_id in seen_record_ids:
                continue
            seen_record_ids.add(record_id)
            try:
                retrieval_score = float(
                    hit.get("unified_score")
                    if hit.get("unified_score") is not None
                    else hit.get("score", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                retrieval_score = 0.0
            score_breakdown_raw = hit_meta.get("score_breakdown") or {}
            score_breakdown: dict[str, float] = {}
            if isinstance(score_breakdown_raw, Mapping):
                for key, value in score_breakdown_raw.items():
                    try:
                        score_breakdown[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue
            # The contract requires non-empty ``written_at``.
            written_at = str(hit.get("created_at", "") or "").strip() or utc_now_iso()
            try:
                entries.append(
                    MemoryProvenanceEntry(
                        memory_id=record_id,
                        source=str(hit_meta.get("record_source", "") or "").strip()
                        or "memory",
                        written_at=written_at,
                        retrieval_score=retrieval_score,
                        score_breakdown=score_breakdown,
                    )
                )
            except (ValueError, TypeError) as entry_exc:
                _logger.debug(
                    "memory.provenance.skip_entry record_id=%s error=%s",
                    record_id,
                    entry_exc,
                )
                continue
        if not entries:
            return
        trace = TurnProvenanceTrace(
            session_id=session_id,
            turn_id=turn_id,
            recorded_at=utc_now_iso(),
            entries=tuple(entries),
            query=user_message,
        )
        default_provenance_recorder().record_turn_trace(trace)

    def _touch_records(self, records: list[Any]) -> None:
        seen: set[str] = set()
        for record in records:
            record_id = str(getattr(record, "id", "") or "").strip()
            if not record_id or record_id in seen:
                continue
            seen.add(record_id)
            try:
                self._service.touch_last_hit(record_id)
            except Exception as exc:
                self._logger.warning(
                    "memory.touch_last_hit failed agent_id=%s record_id=%s error=%s",
                    self._agent_id,
                    record_id,
                    exc,
                )

    def _recent_session_summaries(
        self, *, session_id: str, query_text: str
    ) -> tuple[bool, list[Any]]:
        first_turn = False
        recent_summaries: list[Any] = []
        if self._session_context is not None:
            try:
                first_turn = (
                    self._session_context.get_turn_count(session_id=session_id) == 0
                )
            except Exception:
                first_turn = False
        if not first_turn or self._preamble_shown.get(session_id, False):
            return first_turn, recent_summaries
        try:
            self.compress_old_summaries(
                max_age_days=self._summary_compression_age_days,
                max_summary_chars=self._summary_compression_max_chars,
            )
        except Exception:
            pass
        if len(query_text) >= 10:
            recent_summaries = self._service.search(
                SearchQueryOptions(
                    query=query_text,
                    scopes=[f"agent:{self._agent_id}"],
                    types=["session_summary"],
                    limit=self._session_handoff_max_summaries,
                )
            )
        else:
            recent_summaries = self._service.list(
                ListQueryOptions(
                    scopes=[f"agent:{self._agent_id}"],
                    types=["session_summary"],
                    limit=self._session_handoff_max_summaries,
                    order_by=RecordOrder.UPDATED_AT_DESC,
                )
            )
        self._preamble_shown[session_id] = True
        return first_turn, recent_summaries

    def _memory_records_for_context(
        self, *, session_id: str, query_text: str, first_turn: bool
    ) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        session_scope = f"session:{session_id}"
        long_term_scopes = self._long_term_scopes()
        query_options = {"query": query_text, "limit": 20}
        agent_records = (
            self._service.search(
                SearchQueryOptions(scopes=long_term_scopes, **query_options)
            )
            if query_text
            else self._service.list(ListQueryOptions(scopes=long_term_scopes, limit=20))
        )
        agent_records = self._rerank_long_term_records(
            agent_records,
            use_search_scores=bool(query_text),
        )
        agent_summary_records = [
            record
            for record in agent_records
            if str(getattr(record, "type", "")) == "session_summary"
        ]
        current_session_summary_records = [
            record
            for record in agent_summary_records
            if self._is_current_session_summary_record(record, session_id=session_id)
        ]
        agent_summary_records = [
            record
            for record in agent_summary_records
            if not self._is_current_session_summary_record(
                record, session_id=session_id
            )
        ]
        agent_records = [
            record
            for record in agent_records
            if str(getattr(record, "type", "")) != "session_summary"
        ]
        if not first_turn and not current_session_summary_records:
            current_session_summary_records = [
                record
                for record in self._service.list(
                    ListQueryOptions(
                        scopes=long_term_scopes,
                        types=["session_summary"],
                        limit=self._session_handoff_max_summaries,
                        order_by=RecordOrder.UPDATED_AT_DESC,
                    )
                )
                if self._is_current_session_summary_record(
                    record, session_id=session_id
                )
            ]
        session_records = (
            self._service.search(
                SearchQueryOptions(query=query_text, scopes=[session_scope], limit=10)
            )
            if query_text
            else self._service.list(ListQueryOptions(scopes=[session_scope], limit=10))
        )
        session_records = self._rerank_long_term_records(
            session_records,
            use_search_scores=bool(query_text),
        )
        session_records = [
            record
            for record in session_records
            if str(getattr(record, "type", "") or "") != "session_summary"
        ]
        return (
            agent_records,
            agent_summary_records,
            current_session_summary_records,
            session_records,
        )

    def _format_context_sections(
        self,
        *,
        limit: int,
        session_id: str,
        recent_summaries: list[Any],
        agent_records: list[Any],
        agent_summary_records: list[Any],
        current_session_summary_records: list[Any],
        session_records: list[Any],
    ) -> tuple[str, str, str, str]:
        parts: list[str] = []
        recent_section = self._format_session_summaries(
            recent_summaries,
            max_chars=limit // 4,
        )
        if recent_section:
            parts.append(recent_section)
        agent_section = _format_records_as_context(
            agent_records,
            header="## Agent Memory",
            max_chars=limit // 2,
        )
        if agent_section:
            parts.append(agent_section)
        session_section = _format_records_as_context(
            session_records,
            header="## Session Memory",
            max_chars=limit // 2,
        )
        if session_section:
            parts.append(session_section)
        current_section = ""
        if current_session_summary_records:
            current_section = self._format_session_summaries(
                current_session_summary_records,
                max_chars=limit // 4,
                current_session=True,
            )
        if current_section:
            parts.append(current_section)
        recalled_section = ""
        if (
            session_id not in self._preamble_shown
            and not recent_section
            and not current_section
            and agent_summary_records
        ):
            recalled_section = self._format_session_summaries(
                agent_summary_records,
                max_chars=limit // 4,
            )
        if recalled_section:
            parts.append(recalled_section)
        return "\n\n".join(parts), recent_section, current_section, recalled_section

    def _memory_hits_from_records(self, records: list[Any]) -> list[dict[str, Any]]:
        memory_hits: list[dict[str, Any]] = []
        for rec in records:
            title = getattr(rec, "title", None) or ""
            content_val = getattr(rec, "content", None) or ""
            if isinstance(content_val, dict):
                content_val = str(content_val.get("text", str(content_val)))
            text = str(title or str(content_val)[:120]).strip()
            if not text:
                continue
            record_meta = dict(getattr(rec, "meta", {}) or {})
            breakdown = record_meta.get("score_breakdown", {})
            score = 0.0
            if isinstance(breakdown, Mapping):
                try:
                    score = float(breakdown.get("unified_score", 0.0) or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
            memory_hits.append(
                {
                    "text": text,
                    "score": score,
                    "unified_score": score,
                    "created_at": getattr(rec, "created_at", None),
                    "meta": {
                        **record_meta,
                        "record_id": str(getattr(rec, "id", "") or ""),
                        "record_source": str(getattr(rec, "source", "") or ""),
                    },
                    "source_group": "memory",
                }
            )
        return memory_hits

    def _record_retrieval_provenance(
        self,
        *,
        session_id: str,
        user_message: str,
        merged_hits: list[dict[str, Any]],
    ) -> None:
        try:
            turn_id = ""
            try:
                turn_id = self._service._resolve_telemetry_turn_id()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                turn_id = ""
            if turn_id and merged_hits:
                self._record_turn_provenance_trace(
                    session_id=session_id,
                    turn_id=turn_id,
                    user_message=user_message,
                    merged_hits=merged_hits,
                )
        except Exception as exc:  # noqa: BLE001
            _logger.debug(
                "memory.provenance.record_turn_trace failed "
                "agent_id=%s session_id=%s error=%s",
                getattr(self, "_agent_id", ""),
                session_id,
                exc,
            )

    def _record_retrieve_hits(
        self, *, session_id: str, retrieve_hits: list[dict[str, Any]]
    ) -> None:
        if self._retrieve_ctl is None or not retrieve_hits:
            self._last_retrieved_items[session_id] = []
            return
        self._last_retrieved_items[session_id] = list(retrieve_hits)
        unit_ids: list[str] = []
        seen_unit_ids: set[str] = set()
        for item in retrieve_hits:
            meta_obj = item.get("meta", {})
            unit_id = (
                str(meta_obj.get("unit_id", "")).strip()
                if isinstance(meta_obj, dict)
                else ""
            )
            if not unit_id or unit_id in seen_unit_ids:
                continue
            seen_unit_ids.add(unit_id)
            unit_ids.append(unit_id)
        if not unit_ids:
            return
        try:
            self._retrieve_ctl.record_hits(unit_ids, observed_at=utc_now_iso())
        except Exception as exc:
            self._logger.warning(
                "memory.retrieval.record_hits failed agent_id=%s session_id=%s error=%s",
                self._agent_id,
                session_id,
                exc,
            )

    def _touch_merged_memory_hits(self, merged_hits: list[dict[str, Any]]) -> None:
        touched_memory_ids: set[str] = set()
        for item in merged_hits:
            if str(item.get("source_group", "") or "") != "memory":
                continue
            item_meta = item.get("meta", {})
            if not isinstance(item_meta, dict):
                continue
            record_id = str(item_meta.get("record_id", "") or "").strip()
            if not record_id or record_id in touched_memory_ids:
                continue
            touched_memory_ids.add(record_id)
            try:
                self._service.touch_last_hit(record_id)
            except Exception as exc:
                self._logger.warning(
                    "memory.touch_last_hit failed agent_id=%s record_id=%s error=%s",
                    self._agent_id,
                    record_id,
                    exc,
                )

    def build_context(self, *, session_id: str, user_message: str) -> str:
        content, _ = self.build_context_with_metadata(
            session_id=session_id,
            user_message=user_message,
        )
        return content

    def build_context_with_metadata(
        self, *, session_id: str, user_message: str
    ) -> tuple[str, dict[str, str]]:
        limit = self._capsule_max_chars
        meta = build_empty_meta("capsule", limit)

        try:
            self._maybe_run_session_lifecycle(session_id=session_id)
            query_text = str(user_message or "").strip()
            first_turn, recent_summaries = self._recent_session_summaries(
                session_id=session_id,
                query_text=query_text,
            )
            (
                agent_records,
                agent_summary_records,
                current_session_summary_records,
                session_records,
            ) = self._memory_records_for_context(
                session_id=session_id,
                query_text=query_text,
                first_turn=first_turn,
            )
            (
                content,
                recent_section,
                current_session_summary_section,
                recalled_session_section,
            ) = self._format_context_sections(
                limit=limit,
                session_id=session_id,
                recent_summaries=recent_summaries,
                agent_records=agent_records,
                agent_summary_records=agent_summary_records,
                current_session_summary_records=current_session_summary_records,
                session_records=session_records,
            )
            if len(content) > limit:
                content = content[:limit]
                meta["memory_envelope_truncated"] = "true"
                meta["memory_envelope_truncation_reasons"] = "capsule_limit"
            meta["memory_envelope_limit_chars"] = str(limit)
            meta["prior_context_present"] = (
                "true"
                if (
                    recent_section
                    or current_session_summary_section
                    or recalled_session_section
                )
                else "false"
            )
            # the query-prose `_record_candidate_retrieval_hits`
            self._touch_records(
                recent_summaries
                + agent_records
                + agent_summary_records
                + current_session_summary_records
                + session_records
            )

            self._trace(
                "memory.context.built",
                {
                    "session_id": session_id,
                    "capsule_chars": len(content),
                    "agent_records": len(agent_records),
                    "agent_session_summaries": len(agent_summary_records),
                    "session_records": len(session_records),
                    "truncated": meta["memory_envelope_truncated"],
                },
            )

            return content, meta

        except Exception as exc:
            self._logger.warning(
                "memory.build_context_with_metadata failed agent_id=%s session_id=%s error=%s",
                self._agent_id,
                session_id,
                exc,
            )
            return "", meta

    def build_retrieval_context(
        self,
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
    ) -> str:
        content, _ = self.build_retrieval_context_with_metadata(
            session_id=session_id,
            user_message=user_message,
            max_chars=max_chars,
        )
        return content

    def build_retrieval_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        limit = max(
            128, max_chars if max_chars is not None else self._retrieval_max_chars
        )
        meta = build_empty_meta("retrieval", limit)
        if not user_message.strip():
            return "", meta
        try:
            self._maybe_run_session_lifecycle(session_id=session_id)
            retrieval_scopes = [f"session:{session_id}", *self._long_term_scopes()]
            results = self._service.search_semantic(
                query=user_message,
                scopes=retrieval_scopes,
                limit=8,
            )
            results = self._filter_retrievable_records(results)
            results = self._prioritize_structured_retrieval_records(results)
            self._pipeline.sync_runtime_state(
                config=self._config,
                ranking_config=self._ranking_config,
                retrieve_ctl=self._retrieve_ctl,
                feedback_boost_on_reference=self._feedback_boost_on_reference,
                trace_fn=self._trace,
            )
            content, meta, retrieve_hits, merged_hits = self._pipeline.rank_and_format(
                self._memory_hits_from_records(results),
                session_id=session_id,
                user_message=user_message,
                max_chars=limit,
                project_id=self._project_id,
            )
            self._record_retrieval_provenance(
                session_id=session_id,
                user_message=user_message,
                merged_hits=merged_hits,
            )
            self._record_retrieve_hits(
                session_id=session_id,
                retrieve_hits=retrieve_hits,
            )
            self._touch_merged_memory_hits(merged_hits)
            return content, meta
        except Exception as exc:
            self._logger.warning(
                "memory.build_retrieval_context_with_metadata failed agent_id=%s session_id=%s error=%s",
                self._agent_id,
                session_id,
                exc,
            )
            return "", meta


__all__ = ["ContextBuildersMixin"]
