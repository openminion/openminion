"""Typed memory evidence selected for prompt context."""

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class MemoryEvidenceOmission:
    item_id: str
    provenance_ids: tuple[str, ...]
    reason: str
    count: int = 1


@dataclass(frozen=True)
class MemoryRetrievalEvidenceItem:
    item_id: str
    provenance_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    rendered_text: str
    estimated_tokens: int
    source_rank: int
    eligibility_facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryRetrievalEvidenceSelection:
    items: tuple[MemoryRetrievalEvidenceItem, ...] = ()
    omissions: tuple[MemoryEvidenceOmission, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def metadata_dict(self) -> dict[str, str]:
        return dict(self.metadata)


def build_memory_retrieval_items(
    hits: list[dict[str, Any]],
    *,
    extract_text: Callable[[dict[str, Any]], str],
    render_item: Callable[..., str],
) -> tuple[MemoryRetrievalEvidenceItem, ...]:
    items: list[MemoryRetrievalEvidenceItem] = []
    for source_rank, hit in enumerate(hits):
        text = extract_text(hit)
        if not text:
            continue
        hit_meta = hit.get("meta", {})
        if not isinstance(hit_meta, Mapping):
            hit_meta = {}
        record_id = str(hit_meta.get("record_id", "") or "").strip()
        unit_id = str(hit_meta.get("unit_id", "") or "").strip()
        item_id = record_id or unit_id
        if not item_id:
            item_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        provenance_ids = tuple(
            dict.fromkeys(value for value in (record_id, unit_id) if value)
        ) or (item_id,)
        rendered = render_item(hit, text=text)
        eligibility = (
            "historical"
            if str(hit_meta.get("record_valid_to", "") or "").strip()
            else "current"
        )
        items.append(
            MemoryRetrievalEvidenceItem(
                item_id=item_id,
                provenance_ids=provenance_ids,
                citation_ids=(record_id,) if record_id else provenance_ids,
                rendered_text=rendered,
                estimated_tokens=max(1, len(rendered) // 4),
                source_rank=source_rank,
                eligibility_facts=(eligibility,),
            )
        )
    return tuple(items)


__all__ = [
    "MemoryEvidenceOmission",
    "MemoryRetrievalEvidenceItem",
    "MemoryRetrievalEvidenceSelection",
    "build_memory_retrieval_items",
]
