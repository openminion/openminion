from __future__ import annotations

import re
from typing import Sequence


def estimate_tokens(text: str) -> int:
    compact = (text or "").strip()
    if not compact:
        return 0
    return max(1, len(compact) // 4)


def split_by_token_windows(
    *,
    text: str,
    min_tokens: int,
    max_tokens: int,
    prefer_paragraphs: bool,
) -> list[tuple[str, int, int]]:
    chunks: list[tuple[str, int, int]] = []
    raw = str(text or "").strip()
    if not raw:
        return chunks

    parts: list[str]
    if prefer_paragraphs:
        parts = [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]
        if not parts:
            parts = [raw]
    else:
        parts = [raw]

    cursor = 0
    current: list[str] = []
    current_tokens = 0
    current_start = 0

    for part in parts:
        part_tokens = max(1, estimate_tokens(part))
        if not current:
            current_start = cursor

        if (
            current
            and (current_tokens + part_tokens > max_tokens)
            and current_tokens >= min_tokens
        ):
            segment = "\n\n".join(current).strip()
            if segment:
                chunks.append((segment, current_start, cursor))
            current = [part]
            current_tokens = part_tokens
            current_start = cursor
        else:
            current.append(part)
            current_tokens += part_tokens

        cursor += part_tokens

    if current:
        segment = "\n\n".join(current).strip()
        if segment:
            chunks.append((segment, current_start, cursor))

    if not chunks:
        total = estimate_tokens(raw)
        chunks.append((raw, 0, total))
    return chunks


def split_into_units(
    *,
    text: str,
    unit_kind: str,
    chunk_min_tokens: int,
    chunk_max_tokens: int,
    doc_group_min_tokens: int,
    doc_group_max_tokens: int,
) -> list[tuple[str, int, int]]:
    if unit_kind == "document":
        tokens = estimate_tokens(text)
        return [(text.strip(), 0, tokens)]

    if unit_kind == "doc_group":
        return split_by_token_windows(
            text=text,
            min_tokens=doc_group_min_tokens,
            max_tokens=doc_group_max_tokens,
            prefer_paragraphs=True,
        )

    return split_by_token_windows(
        text=text,
        min_tokens=chunk_min_tokens,
        max_tokens=chunk_max_tokens,
        prefer_paragraphs=True,
    )


def trim_tokens(text: str, *, max_tokens: int) -> str:
    max_chars = max(1, int(max_tokens)) * 4
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + " ..."


def summarize_text(text: str, *, max_tokens: int) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    sentence_split = re.split(r"(?<=[.!?])\s+", compact)
    candidate = sentence_split[0] if sentence_split else compact
    return trim_tokens(candidate, max_tokens=max_tokens)


def build_context_text(
    *,
    contextual_enabled: bool,
    source_type: str,
    source_ref: str,
    scope: str,
    tags: Sequence[str],
    title: str,
    chunk_text: str,
) -> str:
    if not contextual_enabled:
        return ""

    tags_text = ", ".join(str(tag) for tag in tags if str(tag).strip())
    summary = summarize_text(chunk_text, max_tokens=120)
    prefix = [
        f"source_type={source_type}",
        f"source_ref={source_ref}",
        f"scope={scope}",
    ]
    if title.strip():
        prefix.append(f"title={title.strip()}")
    if tags_text:
        prefix.append(f"tags={tags_text}")
    context = "; ".join(prefix) + f"; summary={summary}"
    return trim_tokens(context, max_tokens=160)
