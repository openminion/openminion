import re

_FACT_PREFIX_RE = re.compile(
    r"^\s*(?:remember|fact|correction)\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)
_FACT_INLINE_RE = re.compile(r"^\s*remember\s+(.+)$", flags=re.IGNORECASE)
_TODO_PREFIX_RE = re.compile(r"^\s*todo\s*:\s*(.+)$", flags=re.IGNORECASE)
_DONE_PREFIX_RE = re.compile(r"^\s*(?:done|todo_done)\s*:\s*(.+)$", flags=re.IGNORECASE)
_REMEMBER_EXPLICIT_RE = re.compile(
    r"^\s*(?:remember|correction)\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)
_TOPIC_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_STOPWORDS = {
    "about",
    "after",
    "agent",
    "also",
    "because",
    "before",
    "between",
    "could",
    "during",
    "from",
    "have",
    "into",
    "just",
    "like",
    "more",
    "should",
    "some",
    "than",
    "that",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "turn",
    "use",
    "used",
    "user",
    "using",
    "with",
    "would",
}


def _normalize_line(raw: str, max_chars: int = 400) -> str:
    stripped = " ".join(raw.split())
    return stripped[:max_chars]


def _normalize_scope(scope: str) -> str:
    s = str(scope or "").strip()
    if s == "global":
        return "global:system"
    return s


def _tokenize_text(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOPIC_TOKEN_RE.findall(str(text or ""))
        if token.lower() not in _STOPWORDS
    }


def _normalize_fact_key(prefix: str, value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return f"{prefix}:{slug[:80] or 'value'}"


__all__ = [
    "_DONE_PREFIX_RE",
    "_FACT_INLINE_RE",
    "_FACT_PREFIX_RE",
    "_REMEMBER_EXPLICIT_RE",
    "_SENTENCE_SPLIT_RE",
    "_STOPWORDS",
    "_TODO_PREFIX_RE",
    "_TOPIC_TOKEN_RE",
    "_normalize_fact_key",
    "_normalize_line",
    "_normalize_scope",
    "_tokenize_text",
]
