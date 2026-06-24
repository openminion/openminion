from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Callable

from openminion.modules.memory.constants import MEMORY_CANDIDATE_STATUS_PROPOSED
from openminion.modules.memory.models import (
    ArtifactRef,
    MemoryCandidate,
    MemoryRelation,
    MemoryRecord,
    MemoryTierTransition,
    _as_candidate_status,
    _as_memory_source,
    _as_memory_tier,
    _as_memory_tier_transition_reason,
    _as_memory_type,
)
from openminion.modules.memory.interfaces import MEMORY_INTERFACE_VERSION
from openminion.modules.memory.storage.base import (
    SearchQueryOptions,
    CandidateListOptions,
    ListQueryOptions,
)
from ..errors import NotFoundError


_NORMALIZED_CODES = {
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "PROMOTION_DENIED",
    "BACKEND_UNAVAILABLE",
    "TIMEOUT",
}


@dataclass
class MemoryTransportError(RuntimeError):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class RemoteMemoryTransport:
    """Operation-level transport with timeout/retry/idempotency contracts."""

    contract_version = MEMORY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float = 5.0,
        max_retries: int = 1,
        auth_token: str = "",
        sender: Callable[[dict[str, Any], float], dict[str, Any]] | None = None,
    ) -> None:
        self._endpoint = str(endpoint or "").strip()
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._auth_token = str(auth_token or "").strip()
        self._sender = sender or self._http_sender

    def call(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise MemoryTransportError(
                code="INVALID_ARGUMENT",
                message="payload must be an object",
                retryable=False,
            )
        envelope = {
            "version": "memory_transport.v1",
            "operation": str(operation or "").strip(),
            "idempotency_key": str(idempotency_key or "").strip(),
            "payload": payload,
        }
        if not envelope["operation"]:
            raise MemoryTransportError(
                code="INVALID_ARGUMENT",
                message="operation is required",
                retryable=False,
            )

        attempts = self._max_retries + 1
        last_error: MemoryTransportError | None = None
        for attempt in range(1, attempts + 1):
            try:
                raw = self._sender(envelope, self._timeout_seconds)
                return self._normalize_response(raw)
            except TimeoutError as exc:
                last_error = MemoryTransportError(
                    code="TIMEOUT",
                    message=f"remote timeout during {operation} (attempt {attempt}/{attempts})",
                    retryable=attempt < attempts,
                    details={"attempt": attempt, "operation": operation},
                )
                if attempt >= attempts:
                    raise last_error from exc
            except MemoryTransportError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
            except OSError as exc:
                last_error = MemoryTransportError(
                    code="BACKEND_UNAVAILABLE",
                    message=f"remote backend unavailable during {operation}",
                    retryable=attempt < attempts,
                    details={"attempt": attempt, "operation": operation},
                )
                if attempt >= attempts:
                    raise last_error from exc
        if last_error is not None:
            raise last_error
        raise MemoryTransportError(
            code="BACKEND_UNAVAILABLE",
            message=f"remote backend unavailable during {operation}",
            retryable=False,
        )

    def _normalize_response(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise MemoryTransportError(
                code="INVALID_ARGUMENT",
                message="remote response must be an object",
                retryable=False,
            )
        ok = bool(raw.get("ok", True))
        if ok:
            payload = raw.get("data", raw)
            if isinstance(payload, dict):
                return payload
            raise MemoryTransportError(
                code="INVALID_ARGUMENT",
                message="remote success response must include object data",
                retryable=False,
            )
        error_payload = raw.get("error")
        if not isinstance(error_payload, dict):
            raise MemoryTransportError(
                code="INVALID_ARGUMENT",
                message="remote error payload is invalid",
                retryable=False,
            )
        code = str(error_payload.get("code", "BACKEND_UNAVAILABLE")).strip().upper()
        if code not in _NORMALIZED_CODES:
            code = "BACKEND_UNAVAILABLE"
        message = str(error_payload.get("message", "remote request failed"))
        retryable = code in {"BACKEND_UNAVAILABLE", "TIMEOUT"}
        raise MemoryTransportError(
            code=code,
            message=message,
            retryable=retryable,
            details=error_payload.get("details")
            if isinstance(error_payload.get("details"), dict)
            else {},
        )

    def _http_sender(
        self, envelope: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        data = json.dumps(envelope).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            method="POST",
            data=data,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                **(
                    {"authorization": f"Bearer {self._auth_token}"}
                    if self._auth_token
                    else {}
                ),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8").strip()
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise TimeoutError("remote timeout") from exc
            raise OSError(str(exc)) from exc
        if not body:
            return {"ok": True, "data": {}}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MemoryTransportError(
                code="INVALID_ARGUMENT",
                message="remote response is not valid JSON",
                retryable=False,
            ) from exc
        if isinstance(parsed, dict):
            return parsed
        raise MemoryTransportError(
            code="INVALID_ARGUMENT",
            message="remote response root must be an object",
            retryable=False,
        )


class RemoteMemoryStore:
    """MemoryStore-compatible adapter backed by RemoteMemoryTransport."""

    def __init__(self, transport: RemoteMemoryTransport) -> None:
        self._transport = transport

    def _parse_record(self, payload: dict[str, Any]) -> MemoryRecord:
        now = (
            payload.get("updated_at")
            or payload.get("created_at")
            or "1970-01-01T00:00:00Z"
        )
        raw_refs = payload.get("evidence_refs")
        refs: list[ArtifactRef] = []
        if isinstance(raw_refs, list):
            for item in raw_refs:
                if not isinstance(item, dict):
                    continue
                try:
                    refs.append(
                        ArtifactRef(
                            ref=str(item.get("ref", "remote")),
                            mime=str(item.get("mime", "application/octet-stream")),
                            sha256=str(item.get("sha256", "unknown")),
                            size_bytes=max(0, int(item.get("size_bytes", 0) or 0)),
                            label=str(item.get("label")) if item.get("label") else None,
                        )
                    )
                except Exception:
                    continue
        scope = str(payload.get("scope", "session:remote"))
        if ":" not in scope:
            scope = f"session:{scope or 'remote'}"
        record_type = str(payload.get("type", "fact"))
        if not record_type:
            record_type = "fact"
        return MemoryRecord(
            id=str(payload.get("id", f"remote-{Path(scope).name}")),
            scope=scope,
            type=_as_memory_type(record_type),
            content=payload.get(
                "content", {"text": str(payload.get("text", "")) or "remote"}
            ),
            key=str(payload.get("key")) if payload.get("key") else None,
            title=str(payload.get("title")) if payload.get("title") else None,
            tags=[str(item) for item in payload.get("tags", []) if str(item).strip()]
            if isinstance(payload.get("tags"), list)
            else [],
            entities=[
                str(item) for item in payload.get("entities", []) if str(item).strip()
            ]
            if isinstance(payload.get("entities"), list)
            else [],
            source=_as_memory_source(str(payload.get("source", "imported"))),
            confidence=float(payload.get("confidence", 0.5) or 0.5),
            evidence_refs=refs,
            created_at=str(payload.get("created_at", now)),
            updated_at=str(payload.get("updated_at", now)),
            meta=dict(payload.get("meta", {}))
            if isinstance(payload.get("meta"), dict)
            else {},
            last_hit_at=str(payload.get("last_hit_at"))
            if payload.get("last_hit_at")
            else None,
            event_time=str(payload.get("event_time"))
            if payload.get("event_time")
            else str(payload.get("created_at", now)),
            valid_to=str(payload.get("valid_to")) if payload.get("valid_to") else None,
            goal_id=str(payload.get("goal_id")) if payload.get("goal_id") else None,
            tier=str(payload.get("tier", "working") or "working"),
            access_count=max(0, int(payload.get("access_count", 0) or 0)),
            supersedes_id=str(payload.get("supersedes_id"))
            if payload.get("supersedes_id")
            else None,
            superseded_by_id=str(payload.get("superseded_by_id"))
            if payload.get("superseded_by_id")
            else None,
            supersession_reason=str(payload.get("supersession_reason"))
            if payload.get("supersession_reason")
            else None,
            is_deleted=bool(payload.get("is_deleted", False)),
        )

    def _parse_relation(self, payload: dict[str, Any]) -> MemoryRelation:
        return MemoryRelation(
            relation_id=str(payload.get("relation_id", "")),
            source_record_id=str(payload.get("source_record_id", "")),
            target_record_id=str(payload.get("target_record_id", "")),
            relation_type=str(payload.get("relation_type", "related_to")),
            created_at=str(
                payload.get("created_at")
                or payload.get("updated_at")
                or "1970-01-01T00:00:00Z"
            ),
            meta=dict(payload.get("meta", {}))
            if isinstance(payload.get("meta"), dict)
            else {},
        )

    def _parse_candidate(self, payload: dict[str, Any]) -> MemoryCandidate:
        scope = str(payload.get("proposed_scope", "session:remote"))
        if ":" not in scope:
            scope = f"session:{scope or 'remote'}"
        candidate_type = str(payload.get("type", "fact"))
        if not candidate_type:
            candidate_type = "fact"
        return MemoryCandidate(
            candidate_id=str(payload.get("candidate_id", "remote-candidate")),
            session_id=str(payload.get("session_id", "remote-session")),
            proposed_scope=scope,
            type=_as_memory_type(candidate_type),
            content=payload.get("content", {"text": "remote"}),
            tags=[str(item) for item in payload.get("tags", []) if str(item).strip()]
            if isinstance(payload.get("tags"), list)
            else [],
            entities=[
                str(item) for item in payload.get("entities", []) if str(item).strip()
            ]
            if isinstance(payload.get("entities"), list)
            else [],
            source=_as_memory_source(str(payload.get("source", "imported"))),
            confidence=float(payload.get("confidence", 0.5) or 0.5),
            evidence_refs=[],
            status=_as_candidate_status(
                str(payload.get("status", MEMORY_CANDIDATE_STATUS_PROPOSED))
            ),
            key=str(payload.get("key")) if payload.get("key") else None,
            title=str(payload.get("title")) if payload.get("title") else None,
            meta=dict(payload.get("meta", {}))
            if isinstance(payload.get("meta"), dict)
            else {},
            created_at=str(payload.get("created_at"))
            if payload.get("created_at")
            else None,
            updated_at=str(payload.get("updated_at"))
            if payload.get("updated_at")
            else None,
        )

    def _parse_tier_transition(self, payload: dict[str, Any]) -> MemoryTierTransition:
        return MemoryTierTransition(
            transition_id=str(payload.get("transition_id", "")),
            record_id=str(payload.get("record_id", "")),
            scope=str(payload.get("scope", "session:remote")),
            record_type=_as_memory_type(str(payload.get("record_type", "fact"))),
            from_tier=_as_memory_tier(str(payload.get("from_tier", "working"))),
            to_tier=_as_memory_tier(str(payload.get("to_tier", "working"))),
            transition_reason=_as_memory_tier_transition_reason(
                str(payload.get("transition_reason", "manual"))
            ),
            transition_at=str(
                payload.get("transition_at")
                or payload.get("updated_at")
                or "1970-01-01T00:00:00Z"
            ),
            access_count=max(0, int(payload.get("access_count", 0) or 0)),
            meta=dict(payload.get("meta", {}))
            if isinstance(payload.get("meta"), dict)
            else {},
        )

    def put(self, record: MemoryRecord) -> str:
        payload = self._transport.call(
            operation="put",
            payload={"record": record.__dict__},
            idempotency_key=record.id,
        )
        return str(payload.get("record_id", payload.get("id", record.id)))

    def upsert(
        self, scope: str, type: str, key: str, record_patch: dict[str, Any]
    ) -> MemoryRecord:
        payload = self._transport.call(
            operation="upsert",
            payload={
                "scope": scope,
                "type": type,
                "key": key,
                "record_patch": record_patch,
            },
            idempotency_key=f"{scope}:{type}:{key}",
        )
        return self._parse_record(payload.get("record", payload))

    def get(self, record_id: str) -> MemoryRecord | None:
        payload = self._transport.call(
            operation="get",
            payload={"record_id": record_id},
        )
        if not payload:
            return None
        record_payload = payload.get("record", payload)
        if not isinstance(record_payload, dict):
            return None
        return self._parse_record(record_payload)

    def delete(self, record_id: str) -> None:
        self._transport.call(
            operation="delete",
            payload={"record_id": record_id},
            idempotency_key=record_id,
        )

    def tombstone(self, scope: str, type: str, key: str) -> None:
        self._transport.call(
            operation="tombstone",
            payload={"scope": scope, "type": type, "key": key},
            idempotency_key=f"{scope}:{type}:{key}",
        )

    def list(self, options: ListQueryOptions) -> list[MemoryRecord]:
        payload = self._transport.call(
            operation="list",
            payload={
                "scopes": list(options.scopes),
                "types": list(options.types or []),
                "include_invalidated": bool(options.include_invalidated),
                "limit": options.limit,
                "offset": options.offset,
                "order_by": options.order_by.value if options.order_by else None,
            },
        )
        rows = payload.get("records", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_record(item) for item in rows if isinstance(item, dict)]

    def search(self, options: SearchQueryOptions) -> list[MemoryRecord]:
        payload = self._transport.call(
            operation="search",
            payload={
                "query": options.query,
                "scopes": list(options.scopes),
                "types": list(options.types or []),
                "include_invalidated": bool(options.include_invalidated),
                "limit": options.limit,
                "filters": options.filters.__dict__
                if options.filters is not None
                else None,
            },
        )
        rows = payload.get("records", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_record(item) for item in rows if isinstance(item, dict)]

    def list_scopes(self) -> list[str]:
        payload = self._transport.call(
            operation="list_scopes",
            payload={},
        )
        rows = payload.get("scopes", payload)
        if not isinstance(rows, list):
            return []
        return [str(item) for item in rows if str(item or "").strip()]

    def touch_last_hit(self, record_id: str) -> None:
        self._transport.call(
            operation="touch_last_hit",
            payload={"record_id": record_id},
            idempotency_key=record_id,
        )

    def apply_outcome_feedback(
        self,
        record_ids: list[str],
        *,
        outcome: str,
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        payload = self._transport.call(
            operation="apply_outcome_feedback",
            payload={
                "record_ids": list(record_ids),
                "outcome": outcome,
                "command_id": command_id,
                "observed_at": observed_at,
                "feedback_delta": feedback_delta,
            },
            idempotency_key=f"{command_id}:{outcome}",
        )
        return int(payload.get("updated_count", payload.get("count", 0)) or 0)

    def retrieve_by_entities(
        self,
        entities: list[str],
        scopes: list[str],
        *,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        payload = self._transport.call(
            operation="retrieve_by_entities",
            payload={
                "entities": list(entities),
                "scopes": list(scopes),
                "types": list(types or []),
                "limit": limit,
            },
        )
        rows = payload.get("records", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_record(item) for item in rows if isinstance(item, dict)]

    def transition_tier(
        self,
        record_id: str,
        *,
        to_tier: str,
        transition_reason: str,
        transition_at: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransition:
        payload = self._transport.call(
            operation="transition_tier",
            payload={
                "record_id": record_id,
                "to_tier": to_tier,
                "transition_reason": transition_reason,
                "transition_at": transition_at,
                "meta": dict(meta or {}),
            },
            idempotency_key=f"{record_id}:{transition_at}",
        )
        transition_payload = payload.get("transition", payload)
        if not isinstance(transition_payload, dict):
            raise NotFoundError(f"record not found: {record_id}")
        return self._parse_tier_transition(transition_payload)

    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryTierTransition]:
        payload = self._transport.call(
            operation="list_tier_transitions",
            payload={
                "record_id": record_id,
                "scopes": list(scopes or []),
                "limit": limit,
            },
        )
        rows = payload.get("transitions", payload)
        if not isinstance(rows, list):
            return []
        return [
            self._parse_tier_transition(item) for item in rows if isinstance(item, dict)
        ]

    def put_tier_transition(self, transition: MemoryTierTransition) -> str:
        payload = self._transport.call(
            operation="put_tier_transition",
            payload=asdict(transition),
            idempotency_key=transition.transition_id,
        )
        return str(payload.get("transition_id", transition.transition_id))

    def put_relation(self, relation: MemoryRelation) -> str:
        payload = self._transport.call(
            operation="put_relation",
            payload={
                "relation_id": relation.relation_id,
                "source_record_id": relation.source_record_id,
                "target_record_id": relation.target_record_id,
                "relation_type": relation.relation_type,
                "created_at": relation.created_at,
                "meta": dict(relation.meta or {}),
            },
            idempotency_key=relation.relation_id,
        )
        return str(payload.get("relation_id", relation.relation_id))

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRelation]:
        payload = self._transport.call(
            operation="list_relations",
            payload={
                "record_id": record_id,
                "relation_types": list(relation_types or []),
                "limit": limit,
            },
        )
        rows = payload.get("relations", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_relation(item) for item in rows if isinstance(item, dict)]

    def get_related_records(
        self,
        record_id: str,
        scopes: list[str],
        *,
        relation_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        payload = self._transport.call(
            operation="get_related_records",
            payload={
                "record_id": record_id,
                "scopes": list(scopes),
                "relation_types": list(relation_types or []),
                "limit": limit,
            },
        )
        rows = payload.get("records", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_record(item) for item in rows if isinstance(item, dict)]

    def candidate_put(self, candidate: MemoryCandidate) -> str:
        payload = self._transport.call(
            operation="candidate_put",
            payload={"candidate": candidate.__dict__},
            idempotency_key=candidate.candidate_id,
        )
        return str(payload.get("candidate_id", candidate.candidate_id))

    def candidate_get(self, candidate_id: str) -> MemoryCandidate | None:
        payload = self._transport.call(
            operation="candidate_get",
            payload={"candidate_id": candidate_id},
        )
        if not payload:
            return None
        candidate_payload = payload.get("candidate", payload)
        if not isinstance(candidate_payload, dict):
            return None
        return self._parse_candidate(candidate_payload)

    def candidate_list(self, options: CandidateListOptions) -> list[MemoryCandidate]:
        payload = self._transport.call(
            operation="candidate_list",
            payload={
                "session_id": options.session_id,
                "proposed_scope": options.proposed_scope,
                "status": options.status,
                "limit": options.limit,
            },
        )
        rows = payload.get("candidates", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_candidate(item) for item in rows if isinstance(item, dict)]

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate:
        payload = self._transport.call(
            operation="candidate_update",
            payload={"candidate_id": candidate_id, "patch": patch},
            idempotency_key=candidate_id,
        )
        candidate_payload = payload.get("candidate", payload)
        if not isinstance(candidate_payload, dict):
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return self._parse_candidate(candidate_payload)

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        payload = self._transport.call(
            operation="promote_candidate",
            payload={"candidate_id": candidate_id, "target_scope": target_scope},
            idempotency_key=candidate_id,
        )
        record_payload = payload.get("record", payload)
        if not isinstance(record_payload, dict):
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return self._parse_record(record_payload)

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        payload = self._transport.call(
            operation="supersede_by_contradiction",
            payload={
                "old_record_id": old_record_id,
                "new_record_id": new_record_id,
                "reason": reason,
            },
            idempotency_key=f"{old_record_id}:{new_record_id}",
        )
        record_payload = payload.get("record", payload)
        if not isinstance(record_payload, dict):
            raise NotFoundError(f"record not found: {new_record_id}")
        return self._parse_record(record_payload)

    def invalidate(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecord:
        payload = self._transport.call(
            operation="invalidate",
            payload={
                "record_id": record_id,
                "valid_to": valid_to,
                "reason": reason,
            },
            idempotency_key=f"invalidate:{record_id}:{valid_to}",
        )
        record_payload = payload.get("record", payload)
        if not isinstance(record_payload, dict):
            raise NotFoundError(f"record not found: {record_id}")
        return self._parse_record(record_payload)

    def history(self, scope: str, type: str, key: str) -> list[MemoryRecord]:
        payload = self._transport.call(
            operation="history",
            payload={"scope": scope, "type": type, "key": key},
        )
        rows = payload.get("records", payload)
        if not isinstance(rows, list):
            return []
        return [self._parse_record(item) for item in rows if isinstance(item, dict)]


__all__ = [
    "MemoryTransportError",
    "RemoteMemoryTransport",
    "RemoteMemoryStore",
]
