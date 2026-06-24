"""BM25-style candidate narrowing for skill selection."""

import math
import re
from typing import Any

_BM25_K1 = 1.5
_BM25_B = 0.75
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def narrow_catalog_by_bm25(
    catalog: list[dict[str, Any]],
    query: str,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Return up to `top_k` catalog entries ranked by BM25 text overlap."""
    if top_k <= 0:
        return []
    if not catalog:
        return []
    top_catalog = list(catalog[:top_k])

    if not (query or "").strip():
        return top_catalog

    query_tokens = _tokenize(query)
    if not query_tokens:
        return top_catalog

    doc_tokens: list[list[str]] = [_tokenize(_doc_text(entry)) for entry in catalog]
    doc_lengths = [len(tokens) for tokens in doc_tokens]
    total_docs = len(catalog)
    avg_doc_length = (sum(doc_lengths) / total_docs) if total_docs else 0.0

    unique_query_tokens = set(query_tokens)
    doc_freq: dict[str, int] = {token: 0 for token in unique_query_tokens}
    for tokens in doc_tokens:
        seen_in_doc: set[str] = set()
        for tok in tokens:
            if tok in doc_freq and tok not in seen_in_doc:
                doc_freq[tok] += 1
                seen_in_doc.add(tok)

    idf: dict[str, float] = {}
    for token in unique_query_tokens:
        df = doc_freq.get(token, 0)
        idf[token] = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, entry in enumerate(catalog):
        tokens = doc_tokens[idx]
        if not tokens:
            scored.append((0.0, idx, entry))
            continue
        dl = doc_lengths[idx]
        tf_counts: dict[str, int] = {}
        for tok in tokens:
            if tok in unique_query_tokens:
                tf_counts[tok] = tf_counts.get(tok, 0) + 1
        if not tf_counts:
            scored.append((0.0, idx, entry))
            continue

        score = 0.0
        for token, tf in tf_counts.items():
            numerator = tf * (_BM25_K1 + 1)
            denominator = tf + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * (dl / avg_doc_length if avg_doc_length else 0.0)
            )
            if denominator <= 0:
                continue
            score += idf[token] * (numerator / denominator)
        scored.append((score, idx, entry))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _score, _idx, entry in scored[:top_k]]


def _doc_text(entry: dict[str, Any]) -> str:
    parts = [
        str(entry.get("name") or ""),
        str(entry.get("description") or ""),
        str(entry.get("when_to_use") or ""),
    ]
    return " ".join(part for part in parts if part)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())
