"""Whether two tool path spellings can name the same file.

The read cache and the causal ordering both compare path arguments that the
tool runtime resolves against the same workspace base. Identity is inferred
only when a spelling is comparable: internal dot segments and repeated
separators are the normalization the previous lexical comparison already
applied, while a parent segment, a backslash separator or a different
absolute/relative base leaves identity unknown, and both callers treat unknown
as overlap — a redundant cache miss or a serialized batch is cheaper than a
stale read or an unordered write.
"""

from __future__ import annotations


def _comparable(value: str) -> str | None:
    path = value.strip()
    if not path or "\\" in path:
        return None
    absolute = path.startswith("/")
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    joined = "/".join(parts)
    if absolute:
        return f"/{joined}"
    return joined or "."


def paths_overlap(left: str, right: str) -> bool:
    """True when two path arguments may name the same file or directory."""

    left_path = _comparable(left)
    right_path = _comparable(right)
    if left_path is None or right_path is None:
        return True
    if left_path.startswith("/") != right_path.startswith("/"):
        return True
    if left_path == right_path or left_path in (".", "/") or right_path in (".", "/"):
        return True
    return left_path.startswith(f"{right_path}/") or right_path.startswith(
        f"{left_path}/"
    )
