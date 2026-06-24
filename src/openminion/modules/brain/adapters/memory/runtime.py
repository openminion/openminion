import hashlib
import re
from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    _SCOPE_PATTERN,
    ArtifactRef,
)
from openminion.modules.memory.storage.base import (
    ListQueryOptions,
    SearchQueryOptions,
)
from openminion.modules.memory.models import MemoryPatchResult

_FACT_PREFIX_RE = re.compile(r"^\s*(?:remember|fact)\s*:\s*(.+)$", flags=re.IGNORECASE)


class MemctlAdapter:
    """Adapter for memory operations using openminion-memory."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, store: Any, *, agent_id: str | None = None) -> None:
        self._backend = store
        self.store = getattr(store, "_store", store)
        self._agent_id = str(agent_id or "").strip() or "openminion"
        self._list_api = getattr(store, "list", None)
        self._search_api = getattr(store, "search", None)
        self._write_api = getattr(store, "write_record", None)
        self._stage_api = getattr(store, "stage_candidate", None)
        self._outcome_feedback_api = getattr(store, "apply_outcome_feedback", None)
        self._promote_api = getattr(store, "promote_candidate", None)
        self._candidate_get_api = getattr(store, "candidate_get", None)
        self._candidate_update_api = getattr(store, "candidate_update", None)
        self._candidate_list_api = getattr(store, "candidate_list", None)
        self._find_candidate_by_normalized_key_api = getattr(
            store, "find_candidate_by_normalized_key", None
        )
        self._reinforce_candidate_api = getattr(store, "reinforce_candidate", None)
        self._procedure_api = getattr(store, "get_procedure", None)
        self._generation = 0

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        setter = getattr(self._backend, "set_telemetry_context", None)
        if callable(setter):
            setter(session_id=session_id, turn_id=turn_id)

    @property
    def enabled(self) -> bool:
        return True

    def get_runtime_snapshot(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_highlights: int = 5,
    ) -> dict[str, Any]:
        """MRIR-02: Get bounded memory runtime snapshot for introspection.

        Returns a dict with counts, highlights, and degraded markers.
        """
        from openminion.modules.memory.diagnostics.introspection import (
            build_memory_snapshot,
        )

        snapshot = build_memory_snapshot(
            store=self.store,
            session_id=session_id,
            agent_id=agent_id,
            max_highlights=max_highlights,
        )
        return snapshot.model_dump(mode="json")

    def _normalize_scope(self, scope: str) -> str:
        if _SCOPE_PATTERN.match(str(scope or "")):
            return str(scope)
        return f"session:{scope}"

    def _build_artifact_refs(
        self, evidence_refs: list[str] | None
    ) -> list[ArtifactRef]:
        parsed_evidence: list[ArtifactRef] = []
        for ref in evidence_refs or []:
            parsed_evidence.append(
                ArtifactRef(
                    ref=str(ref),
                    mime="application/octet-stream",
                    sha256="unknown",
                    size_bytes=0,
                    label=f"evidence-{ref}",
                )
            )
        return parsed_evidence

    def _record_text(self, record: Any) -> str:
        content = getattr(record, "content", "")
        if isinstance(content, dict):
            for key in ("text", "summary", "value", "note", "content"):
                value = content.get(key)
                if value:
                    return str(value)
        if isinstance(content, str) and content.strip():
            return content
        title = getattr(record, "title", "")
        if title:
            return str(title)
        key = getattr(record, "key", "")
        return str(key or "")

    def _search_records(
        self,
        *,
        query: str,
        scopes: list[str],
        limit: int,
        types: list[str] | None = None,
    ) -> list[Any]:
        if not callable(self._search_api):
            return []
        try:
            return self._search_api(
                SearchQueryOptions(
                    query=query,
                    scopes=scopes,
                    types=types,
                    limit=max(1, int(limit)),
                )
            )
        except Exception:
            return []

    def _list_records(
        self,
        *,
        scopes: list[str],
        limit: int,
        types: list[str] | None = None,
    ) -> list[Any]:
        if not callable(self._list_api):
            return []
        try:
            return self._list_api(
                ListQueryOptions(
                    scopes=scopes,
                    types=types,
                    limit=max(1, int(limit)),
                )
            )
        except Exception:
            return []

    def retrieve(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del filters
        scopes = [f"session:{session_id}", f"agent:{agent_id}"]
        rows = self._search_records(
            query=query,
            scopes=scopes,
            limit=max(1, int(k)),
        )
        return [
            {
                "record_id": str(getattr(item, "id", "") or ""),
                "scope": str(getattr(item, "scope", "") or ""),
                "record_type": str(getattr(item, "type", "") or "memory"),
                "text": self._record_text(item),
                "score": float(getattr(item, "confidence", 0.0) or 0.0),
                "confidence": float(getattr(item, "confidence", 0.0) or 0.0),
                "tags": list(getattr(item, "tags", []) or []),
            }
            for item in rows
            if self._record_text(item).strip()
        ]

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[dict[str, Any]]:
        del mode_name
        scopes = [f"session:{session_id}", f"agent:{agent_id}"]
        rows = self._search_records(
            query=query,
            scopes=scopes,
            types=["fact"],
            limit=max(1, int(limit)),
        )
        return [
            {
                "record_id": str(getattr(item, "id", "") or ""),
                "text": self._record_text(item),
                "score": float(getattr(item, "confidence", 0.0) or 0.0),
                "confidence": float(getattr(item, "confidence", 0.0) or 0.0),
                "record_type": str(getattr(item, "type", "") or "fact"),
                "source": str(getattr(item, "source", "") or ""),
                "tags": list(getattr(item, "tags", []) or []),
                "meta": dict(getattr(item, "meta", {}) or {}),
            }
            for item in rows
            if self._record_text(item).strip()
        ]

    def get_procedure(self, *, procedure_id: str) -> Any | None:
        """Return the typed `MemoryProcedure` from the backing store, or"""
        if not callable(self._procedure_api):
            return None
        try:
            return self._procedure_api(procedure_id=procedure_id)
        except Exception:
            return None

    def apply_outcome_feedback(
        self,
        *,
        record_ids: list[str],
        outcome: str,
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        if not callable(self._outcome_feedback_api):
            return 0
        return int(
            self._outcome_feedback_api(
                record_ids=list(record_ids),
                outcome=str(outcome or "").strip(),
                command_id=str(command_id or "").strip(),
                observed_at=str(observed_at or "").strip(),
                feedback_delta=float(feedback_delta),
            )
            or 0
        )

    def derive_patch_id(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        user_message: str,
    ) -> str:
        raw = f"{session_id}|{run_id}|{request_id}|{(user_message or '').strip()}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"patch-{digest}"

    def _build_capsule_text(
        self,
        *,
        lane: str,
        session_id: str,
        user_message: str,
        limit: int,
    ) -> str:
        facts = self.query_facts(
            session_id=session_id,
            agent_id=self._agent_id,
            query=user_message or "",
            limit=max(1, int(limit)),
        )
        if not facts:
            fallback = self._list_records(
                scopes=[f"session:{session_id}", f"agent:{self._agent_id}"],
                types=["fact"],
                limit=max(1, int(limit)),
            )
            facts = [
                {
                    "record_id": str(getattr(item, "id", "") or ""),
                    "text": self._record_text(item),
                    "score": float(getattr(item, "confidence", 0.0) or 0.0),
                    "confidence": float(getattr(item, "confidence", 0.0) or 0.0),
                }
                for item in fallback
                if self._record_text(item).strip()
            ]
        if not facts:
            return ""
        prefix = (
            "Agent canonical memory (cross-session):"
            if lane == "capsule"
            else "Agent memory (dynamic retrieval):"
        )
        lines = [prefix, "", "Relevant facts:"]
        for item in facts:
            text = str(item.get("text", "")).strip()
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines).strip()

    def build_context(self, *, session_id: str, user_message: str) -> str:
        return self._build_capsule_text(
            lane="capsule",
            session_id=session_id,
            user_message=user_message,
            limit=6,
        )

    def _context_metadata(
        self, *, lane: str, limit_chars: str, text: str
    ) -> dict[str, str]:
        fact_count = str(0 if not text else text.count("\n- "))
        return {
            "memory_envelope_version": "memory_envelope.v1",
            "memory_envelope_lane": lane,
            "memory_envelope_limit_chars": limit_chars,
            "memory_envelope_chars_before": str(len(text)),
            "memory_envelope_chars_after": str(len(text)),
            "memory_envelope_truncated": "false",
            "memory_envelope_truncation_reasons": "",
            "memory_envelope_state_chars": "0",
            "memory_envelope_facts_before": fact_count,
            "memory_envelope_facts_after": fact_count,
            "memory_envelope_tasks_before": "0",
            "memory_envelope_tasks_after": "0",
        }

    def build_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
    ) -> tuple[str, dict[str, str]]:
        text = self.build_context(session_id=session_id, user_message=user_message)
        return text, self._context_metadata(
            lane="capsule",
            limit_chars="1600",
            text=text,
        )

    def build_retrieval_context(
        self,
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
    ) -> str:
        del max_chars
        return self._build_capsule_text(
            lane="retrieval",
            session_id=session_id,
            user_message=user_message,
            limit=4,
        )

    def build_retrieval_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        text = self.build_retrieval_context(
            session_id=session_id,
            user_message=user_message,
            max_chars=max_chars,
        )
        return text, self._context_metadata(
            lane="retrieval",
            limit_chars="900",
            text=text,
        )

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
        del channel, target, assistant_message
        facts_added = 0
        for raw_line in str(user_message or "").splitlines():
            match = _FACT_PREFIX_RE.match(raw_line.strip())
            if match is None:
                continue
            text = str(match.group(1) or "").strip()
            if not text:
                continue
            self.put_record(
                scope=f"session:{session_id}",
                record_type="fact",
                title=text[:80],
                content={"text": text},
                tags=["runtime"],
                evidence_refs=[],
            )
            facts_added += 1
        patch_id = self.derive_patch_id(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            user_message=user_message,
        )
        self._generation += 1
        return MemoryPatchResult(
            facts_added=facts_added,
            todos_added=0,
            todos_completed=0,
            patch_id=patch_id,
            generation=max(0, self._generation),
            replayed_patches=0,
            lock_recovered=False,
        )

    def put_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str:
        scope = self._normalize_scope(scope)
        if callable(self._write_api):
            return str(
                self._write_api(
                    scope=scope,
                    record_type=record_type,
                    title=title,
                    content=content,
                    tags=tags,
                    evidence_refs=evidence_refs,
                )
            )

        import uuid
        from datetime import datetime, timezone

        record = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            scope=scope,  # type: ignore[arg-type]
            type=record_type,  # type: ignore[arg-type]
            title=title,
            content=content,
            tags=tags or [],
            evidence_refs=self._build_artifact_refs(evidence_refs),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return self.store.put(record)

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
    ) -> str:
        scope = self._normalize_scope(scope)
        if callable(self._stage_api):
            return str(
                self._stage_api(
                    scope=scope,
                    record_type=record_type,
                    title=title,
                    content=content,
                    tags=tags,
                    evidence_refs=evidence_refs,
                    confidence=confidence,
                    meta=meta,
                )
            )

        import uuid

        candidate = MemoryCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:12]}",
            session_id=scope.split(":", 1)[-1] if ":" in scope else scope,
            proposed_scope=scope,  # type: ignore[arg-type]
            type=record_type,  # type: ignore[arg-type]
            title=title,
            content=content,
            tags=tags or [],
            confidence=float(confidence) if confidence is not None else 0.5,
            evidence_refs=self._build_artifact_refs(evidence_refs),
            meta=dict(meta or {}),
        )
        return self.store.candidate_put(candidate)

    def list_candidates(
        self,
        *,
        session_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> list[Any]:
        if callable(self._candidate_list_api):
            try:
                from openminion.modules.memory.storage.base import CandidateListOptions

                return list(
                    self._candidate_list_api(
                        CandidateListOptions(
                            session_id=str(session_id or ""),
                            status=status if status else None,
                            limit=max(1, int(limit)),
                        )
                    )
                )
            except Exception:
                return []
        return []

    def promote_candidate(
        self,
        *,
        candidate_id: str,
        target_scope: str,
    ) -> Any:
        if callable(self._promote_api):
            return self._promote_api(candidate_id, target_scope)
        if hasattr(self.store, "promote_candidate"):
            return self.store.promote_candidate(candidate_id, target_scope)
        raise RuntimeError("memory backend does not support candidate promotion")

    def find_candidate_by_normalized_key(
        self,
        *,
        scope: str,
        normalized_key: str,
    ) -> str | None:
        if not callable(self._find_candidate_by_normalized_key_api):
            return None
        try:
            result = self._find_candidate_by_normalized_key_api(
                scope=scope,
                normalized_key=normalized_key,
            )
        except Exception:  # noqa: BLE001 — best-effort lookup
            return None
        if result is None:
            return None
        return str(result)

    def reinforce_candidate(self, *, candidate_id: str) -> Any:
        if not callable(self._reinforce_candidate_api):
            raise RuntimeError(
                "memory backend does not support candidate reinforcement"
            )
        return self._reinforce_candidate_api(candidate_id=candidate_id)

    def candidate_get(self, candidate_id: str) -> Any:
        if callable(self._candidate_get_api):
            return self._candidate_get_api(candidate_id)
        getter = getattr(self.store, "candidate_get", None)
        if callable(getter):
            return getter(candidate_id)
        raise RuntimeError("memory backend does not support candidate lookup")
