"""Phase 1 extraction helpers for memory consolidation."""

from collections import defaultdict
from datetime import datetime, timezone
import re
from typing import Any

from openminion.modules.memory.diagnostics.operability import parse_iso_utc
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ExtractionPayload,
)
from openminion.modules.memory.runtime.consolidation.backend_access import (
    memory_backend,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.config import (
    MEMORY_CONSOLIDATION_CONTENT_PREVIEW_MAX_CHARS,
)

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._=-]{12,}\b"),
)


def _candidate_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "summary", "value", "note", "content"):
            token = str(value.get(key, "") or "").strip()
            if token:
                return token
        return str(value).strip()
    return str(value or "").strip()


def _truncate_preview(
    text: str,
    *,
    limit: int = MEMORY_CONSOLIDATION_CONTENT_PREVIEW_MAX_CHARS,
) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    candidate = normalized[:limit].rstrip()
    boundary = candidate.rfind(" ")
    if boundary > 0:
        candidate = candidate[:boundary].rstrip()
    return candidate


def _redact_secrets(text: str) -> str:
    redacted = str(text or "")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def _candidate_meta(candidate: Any) -> dict[str, Any]:
    meta = getattr(candidate, "meta", {})
    return dict(meta) if isinstance(meta, dict) else {}


def _strip_attr(item: Any, attr_name: str) -> str:
    return str(getattr(item, attr_name, "") or "").strip()


def _meta_text(item: Any, key: str) -> str:
    return str(_candidate_meta(item).get(key, "") or "").strip()


def _attr_or_meta(item: Any, attr_name: str, key: str) -> str:
    return _strip_attr(item, attr_name) or _meta_text(item, key)


def _record_event_time(record: Any) -> str:
    return _strip_attr(record, "event_time") or _strip_attr(record, "created_at")


def _record_valid_to(record: Any) -> str | None:
    return _strip_attr(record, "valid_to") or None


def _backend_list(memory_api: Any, method_name: str, options: Any) -> list[Any]:
    backend = memory_backend(memory_api)
    method = getattr(backend, method_name, None)
    if not callable(method):
        return []
    try:
        return list(method(options))
    except Exception:
        return []


def _list_candidate_objects(
    memory_api: Any,
    *,
    recent_rollout_limit: int,
) -> list[Any]:
    return _backend_list(
        memory_api,
        "candidate_list",
        CandidateListOptions(
            status="proposed",
            limit=max(1, int(recent_rollout_limit)),
        ),
    )


def _list_scope_records(
    memory_api: Any,
    *,
    scope: str,
    record_type: str,
) -> list[Any]:
    return _backend_list(
        memory_api,
        "list",
        ListQueryOptions(
            scopes=[scope],
            types=[record_type],  # type: ignore[list-item]
            include_invalidated=True,
            limit=None,
        ),
    )


def extract_consolidation_payload(
    memory_api: Any,
    *,
    session_id: str,
    agent_id: str,
    recent_rollout_limit: int = 256,
    now: datetime | None = None,
) -> ExtractionPayload:
    target_now = now or datetime.now(timezone.utc)
    candidates = _list_candidate_objects(
        memory_api,
        recent_rollout_limit=recent_rollout_limit,
    )
    candidate_refs: list[dict[str, Any]] = []
    contradiction_hints: list[dict[str, Any]] = []
    duplicate_hints: list[dict[str, Any]] = []
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for candidate in candidates:
        candidate_id = str(getattr(candidate, "candidate_id", "") or "").strip()
        if not candidate_id:
            continue
        title = _strip_attr(candidate, "title")
        record_type = _strip_attr(candidate, "type")
        proposed_scope = _strip_attr(candidate, "proposed_scope")
        preview = _truncate_preview(
            _redact_secrets(_candidate_text(getattr(candidate, "content", "")))
        )
        candidate_ref = {
            "candidate_id": candidate_id,
            "record_type": record_type,
            "title": title,
            "content_preview": preview,
            "confidence": float(getattr(candidate, "confidence", 0.0) or 0.0),
            "created_at": _strip_attr(candidate, "created_at"),
            "source_session": _strip_attr(candidate, "session_id"),
            "proposed_scope": proposed_scope,
            "source": _strip_attr(candidate, "source"),
        }
        candidate_refs.append(candidate_ref)

        cluster_key = (
            _attr_or_meta(candidate, "key", "normalized_key")
            or title.lower()
            or f"{record_type}:{candidate_id}"
        )
        clusters[cluster_key].append(candidate_ref)

        normalized_key = _attr_or_meta(candidate, "key", "normalized_key")
        if normalized_key:
            duplicate_record = None
            finder = getattr(memory_api, "find_record_by_normalized_key", None)
            if callable(finder) and proposed_scope and record_type:
                duplicate_record = finder(
                    scope=proposed_scope,
                    record_type=record_type,
                    normalized_key=normalized_key,
                )
            else:
                for record in _list_scope_records(
                    memory_api,
                    scope=proposed_scope,
                    record_type=record_type,
                ):
                    if str(getattr(record, "key", "") or "").strip() == normalized_key:
                        duplicate_record = record
                        break
            if duplicate_record is not None:
                duplicate_hints.append(
                    {
                        "candidate_id": candidate_id,
                        "normalized_key": normalized_key,
                        "existing_record_id": str(
                            getattr(duplicate_record, "id", "") or ""
                        ).strip(),
                        "existing_event_time": _record_event_time(duplicate_record),
                        "existing_valid_to": _record_valid_to(duplicate_record),
                    }
                )

        claim_key = _attr_or_meta(candidate, "claim_key", "claim_key")
        polarity = _attr_or_meta(candidate, "polarity", "polarity")
        if claim_key and polarity and proposed_scope and record_type:
            for record in _list_scope_records(
                memory_api,
                scope=proposed_scope,
                record_type=record_type,
            ):
                if _meta_text(record, "claim_key") != claim_key:
                    continue
                record_polarity = _meta_text(record, "polarity")
                if not record_polarity or record_polarity == polarity:
                    continue
                valid_to = _record_valid_to(record)
                valid_to_dt = parse_iso_utc(valid_to)
                contradiction_hints.append(
                    {
                        "candidate_id": candidate_id,
                        "record_id": str(getattr(record, "id", "") or "").strip(),
                        "claim_key": claim_key,
                        "candidate_polarity": polarity,
                        "record_polarity": record_polarity,
                        "record_event_time": _record_event_time(record),
                        "record_valid_to": valid_to,
                        "record_is_current": (
                            valid_to_dt is None or valid_to_dt > target_now
                        ),
                    }
                )

    topic_clusters = [
        {
            "cluster_key": cluster_key,
            "candidate_ids": [item["candidate_id"] for item in entries],
            "record_type": entries[0]["record_type"] if entries else "",
            "titles": [item["title"] for item in entries if item["title"]],
        }
        for cluster_key, entries in sorted(clusters.items())
    ]

    return ExtractionPayload(
        session_id=str(session_id or "").strip(),
        agent_id=str(agent_id or "").strip(),
        candidate_refs=candidate_refs,
        topic_clusters=topic_clusters,
        contradiction_hints=contradiction_hints,
        duplicate_hints=duplicate_hints,
        evidence_window={"recent_rollout_limit": max(1, int(recent_rollout_limit))},
    )


def collect_memory_consolidation_candidates(
    memory_api: Any,
    *,
    proposed_scope: str,
    limit: int,
) -> list[dict[str, Any]]:
    backend = memory_backend(memory_api)
    candidate_list = getattr(backend, "candidate_list", None)
    if not callable(candidate_list):
        return []
    try:
        items = list(
            candidate_list(
                CandidateListOptions(
                    proposed_scope=str(proposed_scope or "").strip() or None,
                    status="proposed",
                    limit=max(1, int(limit)),
                )
            )
        )
    except Exception:
        return []

    batch: list[dict[str, Any]] = []
    for candidate in items:
        candidate_id = _strip_attr(candidate, "candidate_id")
        if not candidate_id:
            continue
        session_id = _strip_attr(candidate, "session_id")
        batch.append(
            {
                "candidate_id": candidate_id,
                "record_type": _strip_attr(candidate, "type"),
                "title": _strip_attr(candidate, "title"),
                "content_preview": _truncate_preview(
                    _candidate_text(getattr(candidate, "content", ""))
                ),
                "confidence": float(getattr(candidate, "confidence", 0.0) or 0.0),
                "created_at": _strip_attr(candidate, "created_at"),
                "source_session": session_id,
                "proposed_scope": _strip_attr(candidate, "proposed_scope"),
                "source": _strip_attr(candidate, "source"),
            }
        )
    return batch


__all__ = ["collect_memory_consolidation_candidates", "extract_consolidation_payload"]
