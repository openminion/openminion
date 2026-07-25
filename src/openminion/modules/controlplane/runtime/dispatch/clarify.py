from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ...contracts.inbound import inbound_metadata
from ...contracts.models import InboundMessage

_LOG = logging.getLogger(__name__)
JsonDict = dict[str, Any]


def extract_clarify_answer(inbound: InboundMessage) -> dict[str, str] | None:
    meta = inbound_metadata(inbound)
    raw = meta.get("clarify_answer")
    if not isinstance(raw, dict):
        return None
    answer = str(raw.get("answer", "")).strip()
    if not answer:
        return None
    return {
        "answer": answer,
        "question_id": str(raw.get("question_id", "")).strip(),
        "clarify_id": str(raw.get("clarify_id", "")).strip(),
    }


def extract_clarify_request(
    *,
    brain_output: JsonDict,
    session_id: str,
    trace_id: str,
    fallback_text: str,
) -> JsonDict | None:
    request = brain_output.get("clarify_request")
    if isinstance(request, dict):
        return normalize_clarify_request(
            request=request,
            session_id=session_id,
            trace_id=trace_id,
            fallback_text=fallback_text,
        )
    metadata = brain_output.get("metadata")
    if isinstance(metadata, dict):
        raw = metadata.get("clarify_request")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return normalize_clarify_request(
                    request=parsed,
                    session_id=session_id,
                    trace_id=trace_id,
                    fallback_text=fallback_text,
                )
    return None


def normalize_clarify_request(
    *,
    request: JsonDict,
    session_id: str,
    trace_id: str,
    fallback_text: str,
) -> JsonDict:
    questions = _normalize_questions(request.get("questions"))
    if not questions and fallback_text.strip():
        questions.append(_fallback_question(fallback_text))
    clarify_id = str(request.get("clarify_id", "")).strip() or uuid4().hex
    return {
        "clarify_id": clarify_id,
        "trace_id": str(request.get("trace_id", "")).strip() or trace_id,
        "session_id": str(request.get("session_id", "")).strip() or session_id,
        "questions": questions,
        "blocking": bool(request.get("blocking", True)),
        "defaults_used": request.get("defaults_used", {}),
    }


def _normalize_questions(raw_questions: object) -> list[JsonDict]:
    questions: list[JsonDict] = []
    if not isinstance(raw_questions, list):
        return questions
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        q_text = str(item.get("question", "")).strip()
        if not q_text:
            continue
        questions.append(
            {
                "id": str(item.get("id", "")).strip() or uuid4().hex,
                "type": str(item.get("type", "ambiguous_input") or "ambiguous_input"),
                "question": q_text,
                "options": item.get("options")
                if isinstance(item.get("options"), list)
                else None,
                "default_value": item.get("default_value"),
                "is_blocking": bool(item.get("is_blocking", True)),
            }
        )
    return questions


def _fallback_question(fallback_text: str) -> JsonDict:
    return {
        "id": uuid4().hex,
        "type": "ambiguous_input",
        "question": fallback_text.strip(),
        "options": None,
        "default_value": None,
        "is_blocking": True,
    }


@dataclass
class ClarifyStateManager:
    store: object
    audit_logger: object | None = None
    _pending_by_session: dict[str, JsonDict] = field(default_factory=dict, init=False)

    def hydrate_from_store(self) -> None:
        """Seed the in-memory pending-clarify map from the store on init."""
        lister = getattr(self.store, "list_pending_clarifies", None)
        if lister is None:
            return
        try:
            rows = lister()
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            _LOG.warning("controlplane: failed to hydrate pending clarifies: %s", exc)
            return
        if not rows:
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            session_id = str(row.get("session_id", "")).strip()
            if not session_id:
                continue
            self._pending_by_session[session_id] = dict(row)

    def get(self, session_id: str) -> JsonDict | None:
        return self._pending_by_session.get(session_id)

    @property
    def pending_by_session(self) -> dict[str, JsonDict]:
        return self._pending_by_session

    def set(self, session_id: str, payload: JsonDict) -> None:
        self._pending_by_session[session_id] = dict(payload)
        setter = getattr(self.store, "set_pending_clarify", None)
        if setter is None:
            return
        try:
            setter(session_id, dict(payload))
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            _LOG.warning(
                "controlplane: failed to persist pending clarify (%s): %s",
                session_id,
                exc,
            )

    def clear(self, session_id: str) -> None:
        self._pending_by_session.pop(session_id, None)
        clearer = getattr(self.store, "clear_pending_clarify", None)
        if clearer is None:
            return
        try:
            clearer(session_id)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            _LOG.warning(
                "controlplane: failed to clear pending clarify (%s): %s",
                session_id,
                exc,
            )
