from __future__ import annotations

import hashlib
import json
from typing import Any

from openminion.base.constants import STATE_KEY_WORKING


def _build_clarify_request_payload(
    *,
    step_out: Any,
    session_id: str,
    trace_id: str | None,
) -> dict[str, Any] | None:
    if str(getattr(step_out, "status", "")).strip().lower() != "waiting_user":
        return None
    working_state = getattr(step_out, STATE_KEY_WORKING, None)
    unresolved = getattr(working_state, "unresolved_clarify_items", [])
    questions: list[dict[str, Any]] = []
    if isinstance(unresolved, list):
        for raw in unresolved:
            if hasattr(raw, "model_dump"):
                item = raw.model_dump(mode="json")
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                continue
            q_text = str(item.get("question", "")).strip()
            if not q_text:
                continue
            q_id = (
                str(item.get("id", "")).strip()
                or hashlib.sha1(q_text.encode("utf-8")).hexdigest()[:12]
            )
            options = item.get("options")
            questions.append(
                {
                    "id": q_id,
                    "type": str(
                        item.get("type", "ambiguous_input") or "ambiguous_input"
                    ),
                    "question": q_text,
                    "reason_code": str(item.get("reason_code", "") or ""),
                    "source": str(item.get("source", "") or ""),
                    "options": options if isinstance(options, list) else None,
                    "default_value": item.get("default_value"),
                    "is_blocking": bool(item.get("is_blocking", True)),
                }
            )
    if not questions:
        return None
    trace_value = str(trace_id or getattr(working_state, "trace_id", "") or "").strip()
    clar_seed = f"{session_id}:{trace_value}:{','.join(q['id'] for q in questions)}"
    clarify_id = hashlib.sha1(clar_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "clarify_id": clarify_id,
        "trace_id": trace_value,
        "session_id": session_id,
        "blocking": True,
        "questions": questions,
        "defaults_used": {},
    }


def _attach_clarify_request_metadata(
    *,
    metadata: dict[str, str],
    clarify_request: dict[str, Any] | None,
) -> None:
    if clarify_request is None:
        return
    metadata["clarify_request"] = json.dumps(
        clarify_request,
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    metadata["clarify_id"] = str(clarify_request.get("clarify_id", ""))
    metadata["clarify_question_count"] = str(len(clarify_request.get("questions", [])))
