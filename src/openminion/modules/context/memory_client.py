from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterable

from openminion.modules.memory.interfaces import ListQueryOptions, RecordOrder

from .contracts import CONTEXT_CLIENT_INTERFACE_VERSION
from .schemas import FactRecord, MemoryCard, RecentSessionArtifactRef


SESSION_START_RECALL_TYPES = (
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
)


def build_mid_session_recall_query(
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
    seen: set[str] = set()

    def append(values: Iterable[str]) -> None:
        for value in values:
            token = str(value or "").strip()
            if not token or token in seen:
                continue
            tokens.append(token)
            seen.add(token)

    append(latest_user_message.split())
    append(intent_ids)
    append(intent_statuses)
    if active_skill_id:
        append([active_skill_id])
    append(resolved_skill_ids)
    if plan_cursor > 0:
        append([f"cursor-{plan_cursor}"])
    append(plan_step_ids)
    append(recent_tool_families)
    return " ".join(tokens)


def parse_memory_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def recent_session_artifact_from_record(
    item: Any,
    *,
    current_session_id: str,
) -> RecentSessionArtifactRef | None:
    content = getattr(item, "content", None)
    payload = content if isinstance(content, dict) else {}
    meta = getattr(item, "meta", None)
    metadata = meta if isinstance(meta, dict) else {}
    session_id = str(
        payload.get("session_id")
        or payload.get("source_session_id")
        or metadata.get("session_id")
        or metadata.get("source_session_id")
        or ""
    ).strip()
    if not session_id or session_id == current_session_id:
        return None
    artifact_path = str(
        payload.get("artifact_path")
        or payload.get("artifact_ref")
        or metadata.get("artifact_path")
        or metadata.get("artifact_ref")
        or ""
    ).strip()
    if not artifact_path:
        for evidence_ref in list(getattr(item, "evidence_refs", []) or []):
            artifact_path = str(getattr(evidence_ref, "ref", "") or "").strip()
            if artifact_path:
                break
    record_id = str(
        getattr(item, "record_id", "") or getattr(item, "id", "") or ""
    ).strip()
    if not record_id or not artifact_path:
        return None
    raw_turn_index = (
        payload.get("turn_index")
        if "turn_index" in payload
        else metadata.get("turn_index")
    )
    try:
        turn_index = max(0, int(raw_turn_index or 0))
    except (TypeError, ValueError):
        turn_index = 0
    return RecentSessionArtifactRef(
        record_id=record_id,
        artifact_type=str(
            payload.get("artifact_type") or metadata.get("artifact_type") or "artifact"
        ).strip()
        or "artifact",
        artifact_path=artifact_path,
        artifact_digest=str(
            payload.get("artifact_digest")
            or payload.get("digest_hash")
            or metadata.get("artifact_digest")
            or metadata.get("digest_hash")
            or ""
        ).strip(),
        session_id=session_id,
        turn_index=turn_index,
        tool_name=str(
            payload.get("tool_name") or metadata.get("tool_name") or ""
        ).strip(),
    )


def select_recent_session_artifacts(
    records: Iterable[Any],
    *,
    current_session_id: str,
    max_results: int,
    max_session_age: int,
    now: datetime | None = None,
) -> list[RecentSessionArtifactRef]:
    if max_results <= 0:
        return []
    current_time = now or datetime.now(timezone.utc)
    cutoff = (
        current_time - timedelta(days=max_session_age) if max_session_age > 0 else None
    )
    selected: list[RecentSessionArtifactRef] = []
    seen_record_ids: set[str] = set()
    for item in records:
        updated_at = parse_memory_timestamp(getattr(item, "updated_at", None))
        if cutoff is not None and (updated_at is None or updated_at < cutoff):
            continue
        raw_expires_at = getattr(item, "expires_at", None)
        expires_at = parse_memory_timestamp(raw_expires_at)
        if raw_expires_at and (expires_at is None or expires_at <= current_time):
            continue
        artifact = recent_session_artifact_from_record(
            item,
            current_session_id=current_session_id,
        )
        if artifact is None or artifact.record_id in seen_record_ids:
            continue
        seen_record_ids.add(artifact.record_id)
        selected.append(artifact)
        if len(selected) >= max_results:
            break
    return selected


def _record_text(item: Any) -> str:
    content = getattr(item, "content", "")
    if isinstance(content, dict):
        text = (
            content.get("summary_text") or content.get("text") or content.get("value")
        )
        if text:
            return str(text).strip()
        return json.dumps(content, sort_keys=True, default=str)
    return str(content or "").strip()


def _memory_card_from_record(item: Any) -> MemoryCard | None:
    text = _record_text(item)
    record_id = str(
        getattr(item, "record_id", "") or getattr(item, "id", "") or ""
    ).strip()
    if not text or not record_id:
        return None
    score = getattr(item, "score", None)
    if score is None:
        score = getattr(item, "confidence", 0.0) or 0.0
    record_type = str(
        getattr(item, "record_type", "") or getattr(item, "type", "") or "memory"
    )
    meta = dict(getattr(item, "meta", {}) or {})
    meta.setdefault("record_confidence", getattr(item, "confidence", 0.0) or 0.0)
    return MemoryCard(
        record_id=record_id,
        record_type=record_type,
        text=text,
        score=float(score),
        pinned=str(getattr(item, "tier", "") or "") == "pinned",
        source=str(getattr(item, "source", "") or ""),
        tags=list(getattr(item, "tags", []) or []),
        meta=meta,
    )


class ContextMemoryClientAdapter:
    """Expose gateway memory through the ContextCtl memory contract."""

    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def __init__(self, memory: Any) -> None:
        self._memory = memory

    def _cards(
        self,
        *,
        session_id: str,
        query: str,
        scopes: list[str],
    ) -> list[MemoryCard]:
        hits, _ = self._memory.recall_context(
            session_id=session_id,
            query=query,
            scopes=scopes,
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
        del agent_id, mode_name
        cards = self._cards(
            session_id=session_id,
            query=query,
            scopes=self._memory.context_scopes(session_id=session_id),
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
        del agent_id, mode_name
        return self._cards(
            session_id=session_id,
            query=query,
            scopes=self._memory.context_scopes(session_id=session_id),
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
        del session_id, agent_id, query, mode_name
        if turn_index != 0 or limit <= 0:
            return []
        records = self._memory.list_records(
            ListQueryOptions(
                scopes=self._memory.context_scopes(),
                types=list(SESSION_START_RECALL_TYPES),
                limit=limit,
                order_by=RecordOrder.UPDATED_AT_DESC,
            )
        )
        cards: list[MemoryCard] = []
        seen: set[str] = set()
        for record in records:
            card = _memory_card_from_record(record)
            if card is None or card.record_id in seen:
                continue
            seen.add(card.record_id)
            cards.append(card)
        return cards

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
        del agent_id, mode_name
        if turn_index <= 0:
            return []
        query = build_mid_session_recall_query(
            latest_user_message=latest_user_message,
            intent_ids=intent_ids,
            intent_statuses=intent_statuses,
            active_skill_id=active_skill_id,
            resolved_skill_ids=resolved_skill_ids,
            plan_cursor=plan_cursor,
            plan_step_ids=plan_step_ids,
            recent_tool_families=recent_tool_families,
        )
        if not query:
            return []
        return self._cards(
            session_id=session_id,
            query=query,
            scopes=self._memory.context_scopes(session_id=session_id),
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
        del agent_id, mode_name
        records = self._memory.list_records(
            ListQueryOptions(
                scopes=self._memory.context_scopes(include_global=False),
                types=["artifact_digest"],
                limit=max(8, max_results * 4),
                order_by=RecordOrder.UPDATED_AT_DESC,
            )
        )
        return select_recent_session_artifacts(
            records,
            current_session_id=session_id,
            max_results=max_results,
            max_session_age=max_session_age,
        )

    def get_procedure(self, *, procedure_id: str) -> Any | None:
        return self._memory.get_procedure(procedure_id=procedure_id)


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
