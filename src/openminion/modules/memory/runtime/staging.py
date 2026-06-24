"""Memory runtime auto-extraction staging helpers."""

from dataclasses import dataclass
from typing import Any

from openminion.modules.memory.runtime.normalized_keys import (
    build_normalized_key,
    is_valid_normalized_key,
)


AFE_INITIAL_CONFIDENCE = 0.3
AFE_SOURCE_TAG = "auto_extracted"


def _candidate_content_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "summary", "value", "note", "content"):
            candidate = value.get(key)
            if candidate:
                return str(candidate)
        return str(value)
    return str(value or "")


def _normalized_content_equal(left: Any, right: Any) -> bool:
    return " ".join(_candidate_content_text(left).strip().lower().split()) == " ".join(
        _candidate_content_text(right).strip().lower().split()
    )


@dataclass(frozen=True)
class ExtractedCandidateDTO:
    """Pre-built typed candidate from the brain-owned LLM extraction pass."""

    kind: str  # "fact" | "user_preference" | "task"
    normalized_key: str
    title: str
    content: str
    tags: tuple[str, ...] = ()
    model_confidence: float | None = None


@dataclass(frozen=True)
class StageExtractedResult:
    """Structural result of staging a batch of extracted candidates."""

    candidate_ids: tuple[str, ...]
    staged_count: int
    skipped: tuple[dict[str, Any], ...]


_KIND_TO_RECORD_TYPE: dict[str, str] = {
    "fact": "fact",
    "user_preference": "user_preference",
    "task": "task",
}


def stage_extracted_candidates(
    *,
    memory_service: Any,
    session_id: str,
    agent_id: str,
    trace_id: str | None,
    candidates: list[ExtractedCandidateDTO],
    scope_override: str | None = None,
    initial_confidence: float = AFE_INITIAL_CONFIDENCE,
) -> StageExtractedResult:
    """Stage a batch of auto-extracted candidates."""
    try:
        confidence_value = float(initial_confidence)
        if not 0.0 <= confidence_value <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        confidence_value = AFE_INITIAL_CONFIDENCE

    staged_ids: list[str] = []
    skipped: list[dict[str, Any]] = []

    if not candidates:
        return StageExtractedResult(
            candidate_ids=(),
            staged_count=0,
            skipped=(),
        )

    stage_fn = getattr(memory_service, "stage_candidate", None)
    if not callable(stage_fn):
        return StageExtractedResult(
            candidate_ids=(),
            staged_count=0,
            skipped=(
                {
                    "reason": "memory_service_unsupported",
                    "detail": "stage_candidate() not available",
                },
            ),
        )

    find_by_key = getattr(memory_service, "find_candidate_by_normalized_key", None)
    reinforce_fn = getattr(memory_service, "reinforce_candidate", None)
    candidate_get = getattr(memory_service, "candidate_get", None)
    reinforcement_enabled = callable(find_by_key) and callable(reinforce_fn)

    scope = str(scope_override or f"agent:{agent_id}").strip()

    for dto in candidates:
        record_type = _KIND_TO_RECORD_TYPE.get(str(dto.kind or "").strip())
        if record_type is None:
            skipped.append(
                {
                    "reason": "unsupported_kind",
                    "kind": str(dto.kind or ""),
                    "title": dto.title,
                }
            )
            continue

        title = str(dto.title or "").strip()
        content = str(dto.content or "").strip()
        if not title or not content:
            skipped.append(
                {
                    "reason": "empty_title_or_content",
                    "kind": dto.kind,
                    "title": title,
                }
            )
            continue

        key = str(dto.normalized_key or "").strip()
        if not is_valid_normalized_key(key):
            key = build_normalized_key(kind=dto.kind, slug=title)

        model_conf: float | None = None
        if dto.model_confidence is not None:
            try:
                model_conf = float(dto.model_confidence)
            except (TypeError, ValueError):
                model_conf = None

        meta: dict[str, Any] = {
            "source": AFE_SOURCE_TAG,
            "source_kind": dto.kind,
            "source_session_id": session_id,
            "source_agent_id": agent_id,
            "normalized_key": key,
        }
        if trace_id:
            meta["source_trace_id"] = trace_id
        if model_conf is not None:
            meta["model_declared_confidence"] = model_conf

        if reinforcement_enabled:
            try:
                existing = find_by_key(scope=scope, normalized_key=key)
            except Exception:  # noqa: BLE001
                existing = None
            if existing:
                if callable(candidate_get):
                    try:
                        existing_candidate = candidate_get(str(existing))
                    except Exception:  # noqa: BLE001
                        existing_candidate = None
                    if existing_candidate is not None and not _normalized_content_equal(
                        content,
                        getattr(existing_candidate, "content", ""),
                    ):
                        existing = None
            if existing:
                try:
                    reinforce_fn(candidate_id=existing)
                    staged_ids.append(str(existing))
                    continue
                except Exception:  # noqa: BLE001
                    pass

        try:
            candidate_id = stage_fn(
                scope=scope,
                record_type=record_type,
                title=title,
                content=content,
                tags=list(dto.tags or ()),
                evidence_refs=None,
                confidence=confidence_value,
                meta=meta,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort staging
            skipped.append(
                {
                    "reason": "stage_candidate_failed",
                    "kind": dto.kind,
                    "title": title,
                    "error": str(exc),
                }
            )
            continue
        staged_ids.append(str(candidate_id))

    return StageExtractedResult(
        candidate_ids=tuple(staged_ids),
        staged_count=len(staged_ids),
        skipped=tuple(skipped),
    )


__all__ = [
    "AFE_INITIAL_CONFIDENCE",
    "AFE_SOURCE_TAG",
    "ExtractedCandidateDTO",
    "StageExtractedResult",
    "stage_extracted_candidates",
]
