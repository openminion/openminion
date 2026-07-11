from dataclasses import dataclass
import re
from typing import Any

from openminion.modules.memory.models import MemoryType
from openminion.modules.memory.runtime.extraction.text import (
    _FACT_INLINE_RE,
    _FACT_PREFIX_RE,
    _DONE_PREFIX_RE,
    _REMEMBER_EXPLICIT_RE,
    _TODO_PREFIX_RE,
    _normalize_line,
)

_EXPLICIT_MEMORY_TYPE_PREFIXES: tuple[tuple[str, MemoryType], ...] = (
    ("correction memory:", "correction"),
    ("preference memory:", "user_preference"),
    ("project memory:", "project_convention"),
    ("fact memory:", "fact"),
)
_EXPLICIT_REMEMBER_TAIL_RE = re.compile(
    r"\s+remember\s+this\s+instead\.?\s*$",
    flags=re.IGNORECASE,
)
_EXPLICIT_USER_EMAIL_RE = re.compile(
    r"^(?:my\s+)?(?:work\s+)?email\s+is\s+(?P<email>[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\.?$",
    flags=re.IGNORECASE,
)
_AGENT_MEMORY_EXACT_VALUE_GUIDANCE = (
    "  Note: copy remembered values verbatim when answering exact-value questions."
)


@dataclass(frozen=True)
class ExplicitDurableFactProjection:
    record_type: MemoryType
    title: str
    content: str
    normalized_key: str
    source_fact_text: str


def _extract_facts_todos_done(
    user_message: str,
) -> tuple[list[str], bool, list[str], list[str]]:
    facts: list[str] = []
    has_explicit_remember = False
    todos_add: list[str] = []
    todos_done: list[str] = []

    for raw_line in str(user_message or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fact_match = _FACT_PREFIX_RE.match(line)
        if fact_match:
            value = _normalize_line(fact_match.group(1))
            if value:
                facts.append(value)
                if _REMEMBER_EXPLICIT_RE.match(line):
                    has_explicit_remember = True
            continue
        todo_match = _TODO_PREFIX_RE.match(line)
        if todo_match:
            value = _normalize_line(todo_match.group(1))
            if value:
                todos_add.append(value)
            continue
        done_match = _DONE_PREFIX_RE.match(line)
        if done_match:
            value = _normalize_line(done_match.group(1))
            if value:
                todos_done.append(value)
            continue
        inline_match = _FACT_INLINE_RE.match(line)
        if inline_match:
            value = _normalize_line(inline_match.group(1))
            if value:
                facts.append(value)

    return facts, has_explicit_remember, todos_add, todos_done


def _format_records_as_context(
    records: list[Any],
    *,
    header: str,
    max_chars: int,
) -> str:
    if not records:
        return ""
    lines = [header]
    if header == "## Agent Memory":
        lines.append(_AGENT_MEMORY_EXACT_VALUE_GUIDANCE)
    for rec in records:
        title = getattr(rec, "title", None) or ""
        content = getattr(rec, "content", None) or ""
        if isinstance(content, dict):
            content = str(content.get("text", content.get("value", str(content))))
        content_text = str(content or "").strip()
        title_text = str(title or "").strip()
        if title_text and content_text and title_text.lower() != content_text.lower():
            text = f"{title_text}: {content_text}"
        else:
            text = title_text or content_text[:120]
        rtype = getattr(rec, "type", "")
        prefix = "📌" if rtype == "pin" else "•"
        lines.append(f"  {prefix} {text}")
    joined = "\n".join(lines)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n  [truncated]"
    return joined


def _content_text(content: Any) -> str:
    if isinstance(content, dict):
        if "summary_text" in content:
            return str(content.get("summary_text", "") or "").strip()
        if "text" in content:
            return str(content.get("text", "") or "").strip()
        return str(content).strip()
    return str(content or "").strip()


def explicit_memory_type_from_content(content: str) -> MemoryType:
    normalized = " ".join(str(content or "").split()).strip()
    lowered = normalized.lower()
    if not lowered:
        return "fact"
    for prefix, record_type in _EXPLICIT_MEMORY_TYPE_PREFIXES:
        if lowered.startswith(prefix):
            return record_type
    return "fact"


def explicit_durable_fact_projection_from_content(
    content: str,
) -> ExplicitDurableFactProjection | None:
    normalized = _normalize_line(str(content or ""))
    if not normalized:
        return None
    candidate = _EXPLICIT_REMEMBER_TAIL_RE.sub("", normalized).strip()
    if not candidate:
        return None
    email_match = _EXPLICIT_USER_EMAIL_RE.match(candidate)
    if email_match is None:
        return None
    email_value = str(email_match.group("email") or "").strip().lower()
    if not email_value:
        return None
    return ExplicitDurableFactProjection(
        record_type="fact",
        title="User email address",
        content=email_value,
        normalized_key="fact:user_email",
        source_fact_text=candidate,
    )


__all__ = [
    "_content_text",
    "_extract_facts_todos_done",
    "_format_records_as_context",
    "ExplicitDurableFactProjection",
    "explicit_durable_fact_projection_from_content",
    "explicit_memory_type_from_content",
]
