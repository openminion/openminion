import uuid
from typing import Any

from openminion.modules.memory.models import MemoryPatchResult
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.services.agent.memory.extraction import (
    _extract_facts_todos_done,
    explicit_durable_fact_projection_from_content,
    explicit_memory_type_from_content,
)


class TurnRecordingMixin:
    def _promote_candidate_safe(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: str,
        tags: list[str],
        confidence: float | None,
        meta: dict[str, Any] | None,
        trace_event: str,
        trace_payload: dict[str, Any],
    ) -> bool:
        try:
            candidate_id = self._service.stage_candidate(
                scope=scope,
                record_type=record_type,
                title=title,
                content=content,
                tags=tags,
                confidence=confidence,
                meta=meta,
            )
            resolved_confidence = float(confidence) if confidence is not None else 0.5
            self._service.candidate_update(
                candidate_id,
                {
                    "status": "approved",
                    "confidence": resolved_confidence,
                },
            )
            self._service.promote_candidate(candidate_id, scope)
            self._trace(trace_event, trace_payload)
            return True
        except Exception as exc:
            self._logger.warning(
                "memory.record_turn: failed to promote candidate scope=%s type=%s error=%s",
                scope,
                record_type,
                exc,
            )
            return False

    def _write_record_safe(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: str,
        tags: list[str],
        entities: list[str] | None = None,
        confidence: float | None = None,
        trace_event: str,
        trace_payload: dict[str, Any],
    ) -> bool:
        del entities
        try:
            self._service.write_record(
                scope=scope,
                record_type=record_type,
                title=title,
                content=content,
                tags=tags,
                confidence=confidence,
            )
            self._trace(trace_event, trace_payload)
            return True
        except Exception as exc:
            self._logger.warning(
                "memory.record_turn: failed to write record scope=%s type=%s error=%s",
                scope,
                record_type,
                exc,
            )
            return False

    def _ingest_retrieve_safe(
        self,
        *,
        scope: str,
        text: str,
        tags: list[str],
        trace_event: str,
        trace_payload: dict[str, Any],
    ) -> bool:
        if self._retrieve_ctl is None:
            return False
        try:
            self._retrieve_ctl.ingest_memory(
                str(uuid.uuid4()),
                text,
                {
                    "scope": scope,
                    "title": text[:120],
                    "tags": list(tags),
                },
            )
            self._trace(trace_event, trace_payload)
            return True
        except Exception as exc:
            self._logger.warning(
                "memory.record_turn: retrieve ingest_memory failed scope=%s error=%s",
                scope,
                exc,
            )
            return False

    def _record_session_facts(self, *, facts: list[str], session_id: str) -> int:
        session_scope = f"session:{session_id}"
        facts_added = 0
        for fact_text in facts:
            if self._write_record_safe(
                scope=session_scope,
                record_type="fact",
                title=fact_text[:120],
                content=fact_text,
                tags=["extracted"],
                trace_event="memory.record.written",
                trace_payload={
                    "scope": session_scope,
                    "type": "fact",
                    "preview": fact_text[:60],
                    "session_id": session_id,
                },
            ):
                facts_added += 1
        return facts_added

    def _record_explicit_durable_facts(
        self, *, facts: list[str], session_id: str
    ) -> None:
        confidence = max(
            float(getattr(self, "_retrieval_min_confidence", 0.0) or 0.0), 0.7
        )
        for fact_text in facts:
            projection = explicit_durable_fact_projection_from_content(fact_text)
            if projection is not None:
                scope = self._scope_for_durable_record(projection.record_type)
                self._promote_candidate_safe(
                    scope=scope,
                    record_type=projection.record_type,
                    title=projection.title,
                    content=projection.content,
                    tags=["extracted", "promoted", "explicit_supersession"],
                    confidence=confidence,
                    meta={
                        "normalized_key": projection.normalized_key,
                        "source": "explicit_turn_fact",
                        "source_fact_text": projection.source_fact_text,
                    },
                    trace_event="memory.record.promoted",
                    trace_payload={
                        "scope": scope,
                        "type": projection.record_type,
                        "preview": projection.content[:60],
                        "normalized_key": projection.normalized_key,
                        "session_id": session_id,
                    },
                )
                continue
            durable_type = explicit_memory_type_from_content(fact_text)
            scope = self._scope_for_durable_record(durable_type)
            self._write_record_safe(
                scope=scope,
                record_type=durable_type,
                title=fact_text[:120],
                content=fact_text,
                tags=["extracted", "promoted"],
                confidence=confidence,
                trace_event="memory.record.written",
                trace_payload={
                    "scope": scope,
                    "type": durable_type,
                    "preview": fact_text[:60],
                    "session_id": session_id,
                },
            )

    def _ingest_explicit_durable_facts(self, *, facts: list[str]) -> None:
        if self._retrieve_ctl is None:
            return
        for fact_text in facts:
            durable_type = explicit_memory_type_from_content(fact_text)
            durable_scope = self._scope_for_durable_record(durable_type)
            self._ingest_retrieve_safe(
                scope=durable_scope,
                text=fact_text,
                tags=["extracted", "promoted"],
                trace_event="memory.ingest_memory.called",
                trace_payload={"scope": durable_scope, "preview": fact_text[:60]},
            )

    def _record_session_todos(self, *, todos_add: list[str], session_id: str) -> int:
        session_scope = f"session:{session_id}"
        todos_added = 0
        for todo_text in todos_add:
            if self._write_record_safe(
                scope=session_scope,
                record_type="task",
                title=todo_text[:120],
                content=todo_text,
                tags=["todo"],
                trace_event="memory.record.written",
                trace_payload={
                    "scope": session_scope,
                    "type": "task",
                    "preview": todo_text[:60],
                    "session_id": session_id,
                },
            ):
                todos_added += 1
        return todos_added

    def _complete_session_todos(self, *, todos_done: list[str], session_id: str) -> int:
        completed = 0
        session_scope = f"session:{session_id}"
        for done_text in todos_done:
            try:
                results = self._service.search(
                    SearchQueryOptions(
                        query=done_text,
                        scopes=[session_scope],
                        types=["task"],
                        limit=5,
                    )
                )
                done_text_lower = done_text.lower()
                for rec in results:
                    rec_title = str(getattr(rec, "title", "") or "").lower()
                    rec_content = str(getattr(rec, "content", "") or "").lower()
                    if done_text_lower in rec_title or done_text_lower in rec_content:
                        self._service._store.delete(rec.id)
                        completed += 1
                        break
            except Exception as exc:
                self._logger.warning(
                    "memory.record_turn: failed to mark done session_id=%s error=%s",
                    session_id,
                    exc,
                )
        return completed

    def record_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        channel: str,
        target: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryPatchResult:
        self._maybe_run_session_lifecycle(session_id=session_id)
        patch_id = self.derive_patch_id(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            user_message=user_message,
        )

        facts, has_explicit_remember, todos_add, todos_done = _extract_facts_todos_done(
            user_message
        )

        facts_auto_extracted = 0
        facts_added = self._record_session_facts(facts=facts, session_id=session_id)
        if has_explicit_remember:
            self._record_explicit_durable_facts(facts=facts, session_id=session_id)
            self._ingest_explicit_durable_facts(facts=facts)

        todos_added = self._record_session_todos(
            todos_add=todos_add,
            session_id=session_id,
        )
        todos_completed = self._complete_session_todos(
            todos_done=todos_done,
            session_id=session_id,
        )
        self._generation += 1

        # lexical retrieval-feedback classifier
        self._last_retrieved_items.pop(session_id, None)

        # the `_record_candidate_turn_signals(user_message=...)`
        self._promote_mature_candidates(
            session_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        if self._candidate_learning_readiness_enabled:
            self._gc_candidates()
        if self._auto_extract_enabled:
            # AFE (brain-owned auto-fact extraction) reports real counts via
            facts_auto_extracted = 0
        notify_count = facts_auto_extracted if self._auto_extract_notify else 0

        self._trace(
            "memory.turn.recorded",
            {
                "session_id": session_id,
                "patch_id": patch_id,
                "facts_added": facts_added,
                "todos_added": todos_added,
                "todos_completed": todos_completed,
                "facts_auto_extracted": facts_auto_extracted,
                "generation": self._generation,
            },
        )

        return MemoryPatchResult(
            facts_added=facts_added,
            todos_added=todos_added,
            todos_completed=todos_completed,
            patch_id=patch_id,
            generation=self._generation,
            facts_auto_extracted=notify_count,
        )


__all__ = ["TurnRecordingMixin"]
