import json
from typing import Any


def extract_clarify_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status", "")).strip().lower()
    direct = payload.get("clarify")
    if status == "waiting_user" and isinstance(direct, dict):
        return _normalize_request(direct)

    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("clarify_request")
        if isinstance(nested, dict):
            return _normalize_request(nested)
        if isinstance(nested, str) and nested.strip():
            try:
                parsed = json.loads(nested)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return _normalize_request(parsed)
    return None


def render_clarify_prompt(
    request: dict[str, Any], *, max_questions: int, answer_prefix: str
) -> str:
    questions = request.get("questions")
    lines = []
    if isinstance(questions, list):
        for item in questions[: max(1, int(max_questions))]:
            if not isinstance(item, dict):
                continue
            q_id = str(item.get("id", "")).strip()
            q_text = str(item.get("question", "")).strip()
            if not q_text:
                continue
            if q_id:
                lines.append(f"- ({q_id}) {q_text}")
            else:
                lines.append(f"- {q_text}")
    if not lines:
        return "Clarification is required before proceeding."

    clarify_id = str(request.get("clarify_id", "")).strip()
    prefix = str(answer_prefix or "/clarify").strip() or "/clarify"
    instructions = (
        f"Reply using `{prefix} {clarify_id} <question_id> <answer>`"
        if clarify_id
        else f"Reply using `{prefix} <question_id> <answer>`"
    )
    return (
        "Clarification is required before proceeding:\n"
        + "\n".join(lines)
        + "\n\n"
        + instructions
    )


def parse_clarify_answer(
    *,
    text: str,
    pending: dict[str, Any] | None,
    answer_prefix: str,
) -> dict[str, str] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    pending_data = pending if isinstance(pending, dict) else {}

    # Callback-style payload: clarify:<clarify_id>:<question_id>:<answer>
    if raw.lower().startswith("clarify:"):
        parts = raw.split(":", 3)
        if len(parts) >= 4:
            return {
                "clarify_id": parts[1].strip(),
                "question_id": parts[2].strip(),
                "answer": parts[3].strip(),
            }
        return None

    prefix = str(answer_prefix or "/clarify").strip().lower() or "/clarify"
    if raw.lower().startswith(prefix + " "):
        remainder = raw[len(prefix) :].strip()
        tokens = remainder.split(maxsplit=2)
        if len(tokens) < 2:
            return None
        if len(tokens) == 2:
            # Allow shorthand: /clarify <question_id> <answer>
            return {
                "clarify_id": str(pending_data.get("clarify_id", "")).strip(),
                "question_id": tokens[0].strip(),
                "answer": tokens[1].strip(),
            }
        return {
            "clarify_id": tokens[0].strip(),
            "question_id": tokens[1].strip(),
            "answer": tokens[2].strip(),
        }

    # Fallback: if there is exactly one pending question, treat raw text as answer.
    questions = pending_data.get("questions")
    if (
        isinstance(questions, list)
        and len(questions) == 1
        and isinstance(questions[0], dict)
    ):
        return {
            "clarify_id": str(pending_data.get("clarify_id", "")).strip(),
            "question_id": str(questions[0].get("id", "")).strip(),
            "answer": raw,
        }
    return None


def build_unknown_clarify_message(
    *,
    provided_id: str,
    expected_id: str | None,
) -> str:
    if expected_id:
        return (
            f"Unknown clarify id `{provided_id}`. "
            f"Please answer using clarify id `{expected_id}` from the latest prompt."
        )
    return f"Unknown clarify id `{provided_id}`. Please wait for a fresh clarification prompt."


def _normalize_request(raw: dict[str, Any]) -> dict[str, Any]:
    clarify_id = str(raw.get("clarify_id", "")).strip()
    trace_id = str(raw.get("trace_id", "")).strip()
    session_id = str(raw.get("session_id", "")).strip()
    questions: list[dict[str, Any]] = []
    source_questions = raw.get("questions")
    if isinstance(source_questions, list):
        for item in source_questions:
            if not isinstance(item, dict):
                continue
            q_text = str(item.get("question", "")).strip()
            if not q_text:
                continue
            questions.append(
                {
                    "id": str(item.get("id", "")).strip(),
                    "question": q_text,
                    "type": str(
                        item.get("type", "ambiguous_input") or "ambiguous_input"
                    ),
                    "options": item.get("options")
                    if isinstance(item.get("options"), list)
                    else None,
                    "default_value": item.get("default_value"),
                    "is_blocking": bool(item.get("is_blocking", True)),
                }
            )
    return {
        "clarify_id": clarify_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "blocking": bool(raw.get("blocking", True)),
        "defaults_used": raw.get("defaults_used", {}),
        "questions": questions,
    }
