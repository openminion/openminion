from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """A candidate evidence item produced by a ContextRetriever."""

    ref: str
    content: str
    score: float = 0.0
    source: str = ""  # retriever name that produced this
    metadata: dict[str, Any] = Field(default_factory=dict)


ContextEvidenceSource = Literal["memory", "knowledge"]


@dataclass(frozen=True)
class ContextEvidenceOmission:
    source_kind: ContextEvidenceSource
    item_id: str
    provenance_ids: tuple[str, ...]
    reason: str
    count: int = 1


@dataclass(frozen=True)
class ContextEvidenceItem:
    source_kind: ContextEvidenceSource
    item_id: str
    provenance_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    rendered_text: str
    estimated_tokens: int
    source_rank: int
    eligibility_facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextEvidencePack:
    items: tuple[ContextEvidenceItem, ...]
    omissions: tuple[ContextEvidenceOmission, ...]
    estimated_tokens: int

    def render_source(self, source_kind: ContextEvidenceSource) -> str:
        return "\n\n".join(
            item.rendered_text for item in self.items if item.source_kind == source_kind
        )
