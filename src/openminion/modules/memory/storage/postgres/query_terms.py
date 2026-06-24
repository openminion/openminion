from typing import Any

from .sql import _FTS_PREFIX_RE, _FTS_TOKEN_RE


def _tsquery_candidates(raw_query: str) -> list[tuple[str, dict[str, Any]]]:
    stripped = str(raw_query or "").strip()
    if not stripped:
        return []
    if (
        stripped.startswith('"')
        and stripped.endswith('"')
        and stripped.count('"') >= 2
        and stripped[1:-1].strip()
    ):
        return [
            (
                "phraseto_tsquery('simple', :query_phrase)",
                {"query_phrase": stripped[1:-1].strip()},
            )
        ]
    candidates = _prefix_candidates(stripped)
    if not candidates:
        tokens = _FTS_TOKEN_RE.findall(stripped)[:16]
        if tokens:
            candidates.append(
                ("to_tsquery('simple', :query_and)", {"query_and": " & ".join(tokens)})
            )
            if len(tokens) > 1:
                candidates.append(
                    (
                        "to_tsquery('simple', :query_or)",
                        {"query_or": " | ".join(tokens)},
                    )
                )
    return _dedupe_candidates(candidates)


def _prefix_candidates(stripped: str) -> list[tuple[str, dict[str, Any]]]:
    if not _FTS_PREFIX_RE.search(stripped):
        return []
    tokens = _FTS_PREFIX_RE.findall(stripped)[:16]
    normalized = [
        f"{token[:-1]}:*" if token.endswith("*") and token[:-1] else token
        for token in tokens
        if token
    ]
    if not normalized:
        return []
    candidates = [
        (
            "to_tsquery('simple', :query_prefix)",
            {"query_prefix": " & ".join(normalized)},
        )
    ]
    if len(normalized) > 1:
        candidates.append(
            (
                "to_tsquery('simple', :query_prefix_or)",
                {"query_prefix_or": " | ".join(normalized)},
            )
        )
    return candidates


def _dedupe_candidates(
    candidates: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    seen: set[tuple[str, tuple[tuple[str, Any], ...]]] = set()
    deduped: list[tuple[str, dict[str, Any]]] = []
    for expression, params in candidates:
        key = (expression, tuple(sorted(params.items())))
        if key not in seen:
            seen.add(key)
            deduped.append((expression, params))
    return deduped


__all__ = ["_tsquery_candidates"]
