"""Modules brain adapters context bridges memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.modules.brain.constants import DEFAULT_MEMORY_DB_FILENAME
from openminion.modules.context.schemas import (
    FactRecord,
    MemoryCard,
    RecentSessionArtifactRef,
)
from openminion.modules.memory.runtime.scope import (
    emit_read_decision,
)

from .shared import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    _extract_text_from_record,
    _lazy_resolve_service,
    _normalized_string_list,
    _resolve_database_path,
)

_CALLER_SEAM_MID_SESSION = "brain.adapters.context.bridges.memory.mid_session"
_CALLER_SEAM_SESSION_START = "brain.adapters.context.bridges.memory.session_start"


def _optional_memory_storage_types() -> tuple[Any, tuple[Any, Any] | None]:
    try:
        from openminion.modules.memory.storage.base import SearchQueryOptions
    except Exception:
        search_query_options = None
    else:
        search_query_options = SearchQueryOptions

    try:
        from openminion.modules.memory.storage.base import ListQueryOptions, RecordOrder
    except Exception:
        list_dependencies = None
    else:
        list_dependencies = (ListQueryOptions, RecordOrder)

    return search_query_options, list_dependencies


SearchQueryOptions, ListDependencies = _optional_memory_storage_types()

_SESSION_START_RECALL_TYPES = [
    "user_preference",
    "procedure",
    "tool_habit",
    "tool_outcome",
    "strategy_outcome",
    "meta_rule_preference",
    "plan_snapshot",
    "meta_insight",
    "correction",
    "session_summary",
    "project_convention",
    "declared_goal",
    "goal_revision",
]


class BridgeMemoryClient:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, backing_store: Any) -> None:
        self._store = backing_store
        self._memory_ctl: Any | None = None

    def _resolve_memoryctl(self) -> Any | None:
        return _lazy_resolve_service(
            self,
            cache_attr="_memory_ctl",
            import_loader=_import_memory_dependencies,
            factory=self._build_memory_ctl,
        )

    def _build_memory_ctl(self, imported: tuple[Any, Any]) -> Any | None:
        memory_service_cls, sqlite_memory_store_cls = imported
        db_path = _resolve_database_path(self._store)
        if db_path is None:
            return None
        memory_db = db_path.parent / DEFAULT_MEMORY_DB_FILENAME
        return memory_service_cls(store=sqlite_memory_store_cls(memory_db))

    def _scope_candidates(self, *, session_id: str, agent_id: str) -> list[str]:
        if agent_id and session_id:
            scopes, _event = emit_read_decision(
                agent_id,
                mode="session_plus_agent",
                session_id=session_id,
                caller_seam=_CALLER_SEAM_MID_SESSION,
            )
            return scopes
        if agent_id:
            scopes, _event = emit_read_decision(
                agent_id,
                mode="agent_only",
                caller_seam=_CALLER_SEAM_MID_SESSION,
            )
            return scopes
        if session_id:
            return [f"session:{session_id}"]
        return ["global:default"]

    def _session_start_recall_scopes(self, *, agent_id: str) -> list[str]:
        if not agent_id:
            return ["global:default"]
        scopes, _event = emit_read_decision(
            agent_id,
            mode="agent_plus_global",
            caller_seam=_CALLER_SEAM_SESSION_START,
        )
        return scopes

    def _degraded_fact(self, *, reason: str) -> list[FactRecord]:
        return [
            FactRecord(
                record_id=f"degraded:{reason}",
                text=f"[memory degraded] {reason}",
                score=0.0,
                confidence=0.0,
                ttl_valid=False,
            )
        ]

    def _degraded_card(self, *, reason: str) -> list[MemoryCard]:
        return [
            MemoryCard(
                record_id=f"degraded:{reason}",
                record_type="degraded",
                text=f"[memory degraded] {reason}",
                score=0.0,
                pinned=False,
            )
        ]

    def _memory_card_from_record(self, item: Any) -> MemoryCard | None:
        record_type = (
            getattr(item, "record_type", "") or getattr(item, "type", "") or "memory"
        )
        text = self._render_record_text(item, record_type=record_type).strip()
        if not text:
            return None
        score_value = getattr(item, "score", None)
        if score_value is None:
            score_value = getattr(item, "confidence", 0.0) or 0.0
        return MemoryCard(
            record_id=getattr(item, "record_id", "") or getattr(item, "id", "") or "",
            record_type=record_type,
            text=text,
            score=float(score_value),
            pinned=bool(getattr(item, "type", "") == "pin"),
            source=str(getattr(item, "source", "") or ""),
            tags=list(getattr(item, "tags", []) or []),
            meta=self._memory_card_meta(item, record_type=record_type),
        )

    def _memory_card_meta(self, item: Any, *, record_type: str) -> dict[str, Any]:
        meta = dict(getattr(item, "meta", {}) or {})
        content = getattr(item, "content", {}) or {}
        if str(record_type).strip().lower() in {
            "decision",
            "improvement_note",
            "strategy_outcome",
            "post_completion_critique",
            "goal_revision",
        } and isinstance(content, dict):
            return {**content, **meta}
        return meta

    def _render_decision_record_text(self, content: dict[str, Any]) -> str:
        parts = [
            f"route={str(content.get('route_chosen') or '').strip() or 'unknown'}",
            f"reason_code={str(content.get('reason_code') or '').strip() or 'unknown'}",
        ]
        sub_intents = _normalized_string_list(content.get("sub_intents"))
        if sub_intents:
            parts.append("sub_intents=" + ",".join(sub_intents[:5]))
        rationale = str(content.get("rationale") or "").strip()
        if rationale:
            parts.append(f"rationale={rationale[:160].rstrip()}")
        return "; ".join(parts)

    def _render_improvement_note_text(self, content: dict[str, Any]) -> str:
        parts = [
            f"status={str(content.get('status') or '').strip() or 'unknown'}",
        ]
        tool_slugs = _normalized_string_list(content.get("tool_slugs"))
        if tool_slugs:
            parts.append("tool_slugs=" + ",".join(tool_slugs[:5]))
        error_slugs = _normalized_string_list(content.get("error_slugs"))
        if error_slugs:
            parts.append("error_slugs=" + ",".join(error_slugs[:5]))
        guidance = str(content.get("guidance") or "").strip()
        if guidance:
            parts.append(f"guidance={guidance[:160].rstrip()}")
        return "; ".join(parts)

    def _render_strategy_outcome_text(self, content: dict[str, Any]) -> str:
        parts = [
            f"strategy_id={str(content.get('strategy_id') or '').strip() or 'unknown'}",
            f"outcome_status={str(content.get('outcome_status') or '').strip() or 'unknown'}",
        ]
        capability_category = str(content.get("capability_category") or "").strip()
        if capability_category:
            parts.append(f"capability_category={capability_category}")
        intent_category = str(content.get("intent_category") or "").strip()
        if intent_category:
            parts.append(f"intent_category={intent_category}")
        termination_reason = str(content.get("termination_reason") or "").strip()
        if termination_reason:
            parts.append(f"termination_reason={termination_reason}")
        return "; ".join(parts)

    def _render_post_completion_critique_text(self, content: dict[str, Any]) -> str:
        parts = [
            f"intent_id={str(content.get('intent_id') or '').strip() or 'unknown'}",
            f"summary={str(content.get('summary') or '').strip() or 'unknown'}",
        ]
        route_chosen = str(content.get("route_chosen") or "").strip()
        if route_chosen:
            parts.append(f"route={route_chosen}")
        lessons = _normalized_string_list(content.get("lessons"))
        if lessons:
            parts.append("lessons=" + " | ".join(lessons[:3]))
        next_time_action = str(content.get("next_time_action") or "").strip()
        if next_time_action:
            parts.append(f"next_time_action={next_time_action}")
        return "; ".join(parts)

    def _render_goal_revision_text(self, content: dict[str, Any]) -> str:
        parts = [
            f"previous_goal={str(content.get('previous_goal') or '').strip() or 'unknown'}",
            f"goal={str(content.get('goal') or '').strip() or 'unknown'}",
        ]
        trigger = str(content.get("trigger") or "").strip()
        if trigger:
            parts.append(f"trigger={trigger[:160].rstrip()}")
        priority = str(content.get("priority") or "").strip()
        if priority:
            parts.append(f"priority={priority}")
        action_type = str(content.get("action_type") or "").strip()
        if action_type:
            parts.append(f"action_type={action_type}")
        policy_verdict = str(content.get("policy_verdict") or "").strip()
        if policy_verdict:
            parts.append(f"policy_verdict={policy_verdict}")
        return "; ".join(parts)

    def _render_record_text(self, item: Any, *, record_type: str) -> str:
        content = getattr(item, "content", {}) or {}
        if str(record_type).strip().lower() == "decision" and isinstance(content, dict):
            return self._render_decision_record_text(content)
        if str(record_type).strip().lower() == "improvement_note" and isinstance(
            content, dict
        ):
            return self._render_improvement_note_text(content)
        if str(record_type).strip().lower() == "strategy_outcome" and isinstance(
            content, dict
        ):
            return self._render_strategy_outcome_text(content)
        if str(
            record_type
        ).strip().lower() == "post_completion_critique" and isinstance(content, dict):
            return self._render_post_completion_critique_text(content)
        if str(record_type).strip().lower() == "goal_revision" and isinstance(
            content, dict
        ):
            return self._render_goal_revision_text(content)
        if str(record_type).strip().lower() != "session_summary" or not isinstance(
            content, dict
        ):
            return _extract_text_from_record(item)
        summary_text = str(content.get("summary_text", "") or "").strip()
        decisions = _normalized_string_list(content.get("decisions", []))
        open_questions = _normalized_string_list(content.get("open_questions", []))
        corrections = _normalized_string_list(content.get("corrections", []))
        lines: list[str] = []
        if summary_text:
            lines.append(f"Most relevant prior session: {summary_text}")
        if decisions:
            lines.append("Prior decisions: " + " | ".join(decisions[:3]))
        if corrections:
            lines.append("Prior corrections: " + " | ".join(corrections[:3]))
        if open_questions:
            lines.append(
                "Open questions from earlier: " + " | ".join(open_questions[:3])
            )
        if lines:
            return "\n".join(lines)
        return _extract_text_from_record(item)

    def _ranked_memory_cards(self, results: list[Any]) -> list[MemoryCard]:
        explicit_scores_present = any(
            getattr(item, "score", None) is not None for item in list(results or [])
        )
        try:
            from openminion.modules.memory.models import MemoryRecord
            from openminion.modules.memory.runtime.scorer import score_records

            ranked_pairs: list[tuple[MemoryRecord, float]] = []
            for item in results or []:
                text = _extract_text_from_record(item).strip()
                if not text:
                    continue
                record = MemoryRecord(
                    id=str(
                        getattr(item, "record_id", "") or getattr(item, "id", "") or ""
                    ),
                    scope="session:bridge",
                    type=str(
                        getattr(item, "record_type", "")
                        or getattr(item, "type", "")
                        or "summary"
                    ),
                    content=text,
                    created_at=str(getattr(item, "created_at", "") or ""),
                    updated_at=str(getattr(item, "updated_at", "") or ""),
                    confidence=float(getattr(item, "score", 0.0) or 0.0),
                )
                ranked_pairs.append((record, float(getattr(item, "score", 0.0) or 0.0)))
            ranked_ids = [
                record.id
                for record in score_records(
                    [record for record, _ in ranked_pairs],
                    query_bm25_scores=[score for _record, score in ranked_pairs],
                )
            ]
        except Exception:
            ranked_ids = []

        sort_order = {record_id: idx for idx, record_id in enumerate(ranked_ids)}
        mapped: list[MemoryCard] = []
        for item in results or []:
            card = self._memory_card_from_record(item)
            if card is None:
                continue
            mapped.append(card)
        if explicit_scores_present and sort_order:
            mapped.sort(
                key=lambda card: (
                    sort_order.get(card.record_id, len(sort_order)),
                    str(card.record_id),
                )
            )
        return mapped

    def _build_mid_session_recall_query(
        self,
        *,
        latest_user_message: str,
        intent_ids: list[str],
        intent_statuses: list[str],
        active_skill_id: str | None,
        resolved_skill_ids: list[str],
        plan_cursor: int,
        plan_step_ids: list[str],
        recent_tool_families: list[str],
    ) -> str:
        tokens: list[str] = []

        def _append(values: list[str]) -> None:
            seen = {item for item in tokens}
            for value in values:
                token = str(value or "").strip()
                if not token or token in seen:
                    continue
                tokens.append(token)
                seen.add(token)

        latest_user_tokens = [
            token
            for token in str(latest_user_message or "").strip().split()
            if token.strip()
        ]
        _append(latest_user_tokens)
        _append(intent_ids)
        _append(intent_statuses)
        if active_skill_id:
            _append([active_skill_id])
        _append(resolved_skill_ids)
        if int(plan_cursor or 0) > 0:
            _append([f"cursor-{int(plan_cursor)}"])
        _append(plan_step_ids)
        _append(recent_tool_families)
        return " ".join(tokens).strip()

    def _parse_timestamp(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _recent_session_artifact_from_record(
        self,
        item: Any,
        *,
        current_session_id: str,
    ) -> RecentSessionArtifactRef | None:
        content = getattr(item, "content", None)
        payload = content if isinstance(content, dict) else {}
        meta = getattr(item, "meta", None)
        meta_payload = meta if isinstance(meta, dict) else {}
        session_id = str(
            payload.get("session_id")
            or payload.get("source_session_id")
            or meta_payload.get("session_id")
            or meta_payload.get("source_session_id")
            or ""
        ).strip()
        if not session_id or session_id == current_session_id:
            return None
        artifact_path = str(
            payload.get("artifact_path")
            or payload.get("artifact_ref")
            or meta_payload.get("artifact_path")
            or meta_payload.get("artifact_ref")
            or ""
        ).strip()
        if not artifact_path:
            for ref in list(getattr(item, "evidence_refs", []) or []):
                candidate = str(getattr(ref, "ref", "") or "").strip()
                if candidate:
                    artifact_path = candidate
                    break
        if not artifact_path:
            return None
        turn_raw = (
            payload.get("turn_index")
            if "turn_index" in payload
            else meta_payload.get("turn_index")
        )
        try:
            turn_index = max(0, int(turn_raw or 0))
        except Exception:
            turn_index = 0
        record_id = str(
            getattr(item, "record_id", "") or getattr(item, "id", "") or ""
        ).strip()
        if not record_id:
            return None
        return RecentSessionArtifactRef(
            record_id=record_id,
            artifact_type=str(
                payload.get("artifact_type")
                or meta_payload.get("artifact_type")
                or "artifact"
            ).strip()
            or "artifact",
            artifact_path=artifact_path,
            artifact_digest=str(
                payload.get("artifact_digest")
                or payload.get("digest_hash")
                or meta_payload.get("artifact_digest")
                or meta_payload.get("digest_hash")
                or ""
            ).strip(),
            session_id=session_id,
            turn_index=turn_index,
            tool_name=str(
                payload.get("tool_name") or meta_payload.get("tool_name") or ""
            ).strip(),
        )

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[FactRecord]:
        del mode_name
        if SearchQueryOptions is None:
            return self._degraded_fact(reason="search_query_options_unavailable")
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return self._degraded_fact(reason="memory_backend_unavailable")
        try:
            results = memory_ctl.search(
                options=SearchQueryOptions(
                    query=query,
                    scopes=self._scope_candidates(
                        session_id=session_id,
                        agent_id=agent_id,
                    ),
                    types=["fact"],
                    limit=limit,
                )
            )
            mapped: list[FactRecord] = []
            for item in results or []:
                text = _extract_text_from_record(item).strip()
                if not text:
                    continue
                mapped.append(
                    FactRecord(
                        record_id=getattr(item, "record_id", "")
                        or getattr(item, "id", "")
                        or "",
                        text=text,
                        score=float(getattr(item, "score", 0.0) or 0.0),
                        confidence=float(getattr(item, "confidence", 0.0) or 0.0),
                        ttl_valid=not bool(getattr(item, "is_deleted", False)),
                        record_type=str(getattr(item, "type", "") or "fact"),
                        source=str(getattr(item, "source", "") or ""),
                        tags=list(getattr(item, "tags", []) or []),
                        meta=dict(getattr(item, "meta", {}) or {}),
                    )
                )
            return mapped
        except Exception as exc:
            return self._degraded_fact(reason=f"query_error:{exc}")

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        if SearchQueryOptions is None:
            return self._degraded_card(reason="search_query_options_unavailable")
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return self._degraded_card(reason="memory_backend_unavailable")
        try:
            results = memory_ctl.search(
                options=SearchQueryOptions(
                    query=query,
                    scopes=self._scope_candidates(
                        session_id=session_id,
                        agent_id=agent_id,
                    ),
                    limit=limit,
                )
            )
            return self._ranked_memory_cards(results or [])
        except Exception as exc:
            return self._degraded_card(reason=f"query_error:{exc}")

    def list_cross_session_memory_cards_by_type(
        self,
        *,
        agent_id: str,
        record_types: list[str],
        limit: int,
    ) -> list[MemoryCard]:
        if ListDependencies is None:
            return []
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return []
        list_records = getattr(memory_ctl, "list", None)
        if not callable(list_records):
            return []
        list_query_options_cls, record_order_cls = ListDependencies
        try:
            records = list_records(
                list_query_options_cls(
                    scopes=self._session_start_recall_scopes(agent_id=agent_id),
                    types=[
                        str(item).strip() for item in record_types if str(item).strip()
                    ],
                    limit=max(1, int(limit or 1)),
                    order_by=record_order_cls.UPDATED_AT_DESC,
                )
            )
        except Exception:
            return []
        return self._ranked_memory_cards(list(records or []))

    def recall_session_start_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        turn_index: int,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        del session_id, mode_name
        if int(turn_index or 0) != 0:
            return []
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return []
        scopes = self._session_start_recall_scopes(agent_id=agent_id)
        normalized_query = str(query or "").strip()
        records: list[Any] | None = None
        if normalized_query and SearchQueryOptions is not None:
            try:
                searched = memory_ctl.search(
                    options=SearchQueryOptions(
                        query=normalized_query,
                        scopes=scopes,
                        types=list(_SESSION_START_RECALL_TYPES),
                        limit=max(1, int(limit or 1)),
                    )
                )
                records = list(searched or [])
                if not records:
                    records = None
            except Exception:
                records = None
        if records is None:
            if ListDependencies is None:
                return []
            list_records = getattr(memory_ctl, "list", None)
            if not callable(list_records):
                return []
            list_query_options_cls, record_order_cls = ListDependencies
            try:
                records = list_records(
                    list_query_options_cls(
                        scopes=scopes,
                        types=list(_SESSION_START_RECALL_TYPES),
                        limit=max(1, int(limit or 1)),
                        order_by=record_order_cls.UPDATED_AT_DESC,
                    )
                )
            except Exception:
                return []

        cards = self._ranked_memory_cards(list(records or []))
        deduped: list[MemoryCard] = []
        seen: set[str] = set()
        for card in cards:
            if card.record_id in seen:
                continue
            seen.add(card.record_id)
            deduped.append(card)
        return deduped

    def recall_mid_session_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        turn_index: int,
        latest_user_message: str,
        intent_ids: list[str],
        intent_statuses: list[str],
        active_skill_id: str | None,
        resolved_skill_ids: list[str],
        plan_cursor: int,
        plan_step_ids: list[str],
        recent_tool_families: list[str],
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        del mode_name
        if int(turn_index or 0) <= 0 or SearchQueryOptions is None:
            return []
        query = self._build_mid_session_recall_query(
            latest_user_message=latest_user_message,
            intent_ids=list(intent_ids or []),
            intent_statuses=list(intent_statuses or []),
            active_skill_id=active_skill_id,
            resolved_skill_ids=list(resolved_skill_ids or []),
            plan_cursor=int(plan_cursor or 0),
            plan_step_ids=list(plan_step_ids or []),
            recent_tool_families=list(recent_tool_families or []),
        )
        if not query:
            return []
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return []
        try:
            results = memory_ctl.search(
                options=SearchQueryOptions(
                    query=query,
                    scopes=self._scope_candidates(
                        session_id=session_id,
                        agent_id=agent_id,
                    ),
                    limit=max(1, int(limit or 1)),
                )
            )
        except Exception:
            return []
        return self._ranked_memory_cards(results or [])[: max(1, int(limit or 1))]

    def recall_recent_session_artifacts(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_results: int,
        max_session_age: int,
        mode_name: str | None = None,
    ) -> list[RecentSessionArtifactRef]:
        del mode_name
        if ListDependencies is None:
            return []
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return []
        list_records = getattr(memory_ctl, "list", None)
        if not callable(list_records):
            return []
        list_query_options_cls, record_order_cls = ListDependencies
        try:
            records = list_records(
                list_query_options_cls(
                    scopes=self._session_start_recall_scopes(agent_id=agent_id),
                    types=["artifact_digest"],
                    limit=max(8, max(1, int(max_results or 1)) * 4),
                    order_by=record_order_cls.UPDATED_AT_DESC,
                )
            )
        except Exception:
            return []
        cutoff: datetime | None = None
        if int(max_session_age or 0) > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(max_session_age))
        refs: list[RecentSessionArtifactRef] = []
        seen_record_ids: set[str] = set()
        for item in records or []:
            updated_at = self._parse_timestamp(getattr(item, "updated_at", None))
            if cutoff is not None and updated_at is not None and updated_at < cutoff:
                continue
            ref = self._recent_session_artifact_from_record(
                item,
                current_session_id=session_id,
            )
            if ref is None or ref.record_id in seen_record_ids:
                continue
            seen_record_ids.add(ref.record_id)
            refs.append(ref)
            if len(refs) >= max(1, int(max_results or 1)):
                break
        return refs

    def get_procedure(self, *, procedure_id: str) -> Any | None:
        """Pass through the typed `MemoryProcedure` (or `None`) from the"""
        memory_ctl = self._resolve_memoryctl()
        if memory_ctl is None:
            return None
        try:
            payload = memory_ctl.get_procedure(procedure_id=procedure_id)
        except Exception:
            return None
        if payload is None:
            return None
        if (
            isinstance(payload, dict)
            and str(payload.get("status", "")).strip().lower() == "unsupported"
        ):
            return None
        return payload


def _import_memory_dependencies() -> tuple[Any, Any] | None:
    try:
        from openminion.modules.memory.service import MemoryService
        from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
    except Exception:
        return None
    return MemoryService, SQLiteMemoryStore


__all__ = ["BridgeMemoryClient", "SearchQueryOptions"]
