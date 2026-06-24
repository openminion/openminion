def count_tokens(text: str) -> int:
    """Return a deterministic whitespace token count for plain text."""

    return len(text.split()) if text.strip() else 0
