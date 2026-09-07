from __future__ import annotations

from typing import Any

from .contracts import CONTEXT_CLIENT_INTERFACE_VERSION
from .schemas import FactRecord, MemoryCard, RecentSessionArtifactRef


class ContextMemoryClientAdapter:
    """Expose gateway memory through the ContextCtl memory contract."""

    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def __init__(self, memory: Any) -> None:
        self._memory = memory

    def _cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
    ) -> list[MemoryCard]:
        recall = getattr(self._memory, "_recall_hits", None)
        if not callable(recall):
            return []
        hits, _ = recall(
            session_id=session_id,
            query=query,
            scopes=[f"agent:{agent_id}", f"session:{session_id}"],
        )
        cards: list[MemoryCard] = []
        for hit in hits:
            meta = dict(hit.get("meta", {}) or {})
            cards.append(
                MemoryCard(
                    record_id=str(meta.get("record_id", "")),
                    record_type=str(meta.get("record_type", "memory") or "memory"),
                    text=str(meta.get("record_content", "") or hit.get("text", "")),
                    score=float(hit.get("score", 0.0) or 0.0),
                    pinned=str(meta.get("record_tier", "")) == "pinned",
                    source=str(meta.get("record_source", "")),
                    tags=list(meta.get("record_tags", []) or []),
                    meta=meta,
                )
            )
        return cards

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
        cards = self._cards(
            session_id=session_id,
            agent_id=agent_id,
            query=query,
        )
        return [
            FactRecord(
                record_id=card.record_id,
                text=card.text,
                score=card.score,
                confidence=float(card.meta.get("record_confidence", 0.0) or 0.0),
                ttl_valid=True,
                record_type=card.record_type,
                source=card.source,
                tags=card.tags,
                meta=card.meta,
            )
            for card in cards
            if card.record_type == "fact"
        ][: max(0, limit)]

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        del mode_name
        return self._cards(
            session_id=session_id,
            agent_id=agent_id,
            query=query,
        )[: max(0, limit)]

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
        del mode_name
        if turn_index != 0:
            return []
        return [
            card
            for card in self._cards(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
            )
            if card.record_type == "session_summary"
        ][: max(0, limit)]

    def recall_mid_session_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        turn_index: int,
        intent_ids: list[str],
        intent_statuses: list[str],
        latest_user_message: str,
        active_skill_id: str | None,
        resolved_skill_ids: list[str],
        plan_cursor: int,
        plan_step_ids: list[str],
        recent_tool_families: list[str],
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        del (
            turn_index,
            intent_ids,
            intent_statuses,
            active_skill_id,
            resolved_skill_ids,
            plan_cursor,
            plan_step_ids,
            recent_tool_families,
            mode_name,
        )
        return self._cards(
            session_id=session_id,
            agent_id=agent_id,
            query=latest_user_message,
        )[: max(0, limit)]

    def recall_recent_session_artifacts(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_results: int,
        max_session_age: int,
        mode_name: str | None = None,
    ) -> list[RecentSessionArtifactRef]:
        del session_id, agent_id, max_results, max_session_age, mode_name
        return []

    def get_procedure(self, *, procedure_id: str) -> Any | None:
        return self._memory._service.get_procedure(procedure_id=procedure_id)


class NullMemoryClient:
    """Empty ContextCtl memory client used when memory is unavailable."""

    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_facts(self, **kwargs: Any) -> list[Any]:
        return []

    def query_memory_cards(self, **kwargs: Any) -> list[Any]:
        return []

    def recall_session_start_memory(self, **kwargs: Any) -> list[Any]:
        return []

    def recall_mid_session_memory(self, **kwargs: Any) -> list[Any]:
        return []

    def recall_recent_session_artifacts(self, **kwargs: Any) -> list[Any]:
        return []

    def get_procedure(self, **kwargs: Any) -> None:
        return None
